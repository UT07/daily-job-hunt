r"""The page rules, measured against the compiled PDF.

`shared/composition_policy.py` splits a resume's composition rules by whether
they can be measured, and says of the third case:

    Page count is a third case: countable, but only against a compiled PDF, not
    against LaTeX source. It is carried here and checked by whoever holds the
    PDF; `check_output` deliberately does not pretend to know it from the .tex.

This module is that check. `tailor_resume.py` has told the model "The resume
MUST be exactly TWO PAGES." since it was written and nothing ever read a PDF,
so a resume shipped with a blank page in the middle and cleared every gate.

Two things are measured, and the second is the reason this exists:

1. PAGE COUNT against the caller's expectation — the user's
   ``composition_policy["pages"]``, or an explicit override.

2. A PER-PAGE FLOOR on extractable text. `resumes/fullstack.tex:84` emits
   ``\clearpage`` before Featured Projects so the heading cannot be orphaned
   from its entries; when the model emits no projects the page break fires
   anyway and nothing follows it. The resulting document still has the right
   NUMBER of pages, so a page-count check passes it. Only a per-page floor
   catches it.

WHY 40. Measured 2026-09-30 with tectonic 0.15 + pdfplumber 0.11.9, in
non-space extractable characters per page (the fixtures are in
tests/fixtures/pdf, and tests/unit/test_page_check.py carries the full table):

    full page of a real resume                  3650 - 3713
    page holding three project entries          2538
    page holding Education + Certifications      823
    page holding one project entry               616
    middle page of resumes/sre_devops.tex        395
    tail page holding two certification bullets   106
    tail page holding one certification bullet     54   <- thinnest real page
    ---------------------------------------------------- floor: 40
    page holding "Featured Projects" and nothing    16
    page break with nothing on it at all             0

40 sits in the empty band between the thinnest page that carries real content
(54) and the largest page that carries none (16). It is not a guess at what
"looks empty"; it is the gap between two measurements.

WHY IT REPORTS ITS OWN ABSENCE. `utils/pdf_validator.py` has a page-count check
that has never run once in production: it needs `fitz` (pymupdf), which is in no
requirements file, so it appends a warning and returns ``valid: True``. A check
that reports success when it did not run is not a check. When pdfplumber is
missing here, that is a violation.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from shared.composition_policy import resolve

try:  # pragma: no cover - exercised by monkeypatching the name to None
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None

logger = logging.getLogger(__name__)

# Non-space extractable characters below which a page carries no content. See
# the measurement table in this module's docstring.
MIN_PAGE_CHARS = 40


def page_text_lengths(pdf_path: str | os.PathLike) -> list[int]:
    """Non-space extractable characters on each page, in order.

    Spaces and newlines are excluded because they are a property of the
    typesetting, not of the content: an empty page and a page of a hundred
    line breaks are both empty. ``extract_text()`` returns None (not "") for a
    page with nothing on it, which is the value a naive ``len()`` trips over.

    Raises RuntimeError if pdfplumber is unavailable, and whatever pdfplumber
    raises if the file is not a readable PDF. `check_pdf` turns both into
    violations rather than swallowing them.
    """
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not installed")
    with pdfplumber.open(str(pdf_path)) as pdf:
        return [len("".join((page.extract_text() or "").split())) for page in pdf.pages]


def check_pdf(
    pdf_path: str | os.PathLike,
    policy: dict[str, Any] | None = None,
    expected_pages: int | None = None,
) -> list[str]:
    """Violations in a compiled PDF. An empty list means it complies.

    The page count is asserted only when the caller says what it should be —
    ``expected_pages``, else ``policy["pages"]``. Passing neither leaves the
    count unasserted: a master corpus is legitimately longer than a resume
    composed from it, so a default page count applied to whatever PDF happens
    to arrive would flag the corpus for being itself. The per-page floor runs
    either way, because no document has a use for a blank page.
    """
    if expected_pages is None and policy is not None:
        expected_pages = resolve(policy)["pages"]

    try:
        lengths = page_text_lengths(pdf_path)
    except RuntimeError as exc:
        return [f"the page check could not run: {exc}"]
    except Exception as exc:
        return [f"the compiled PDF could not be read: {type(exc).__name__}: {exc}"]

    violations: list[str] = []
    total = len(lengths)
    if isinstance(expected_pages, int) and total != expected_pages:
        violations.append(f"{total} pages, the policy is {expected_pages}")
    for index, chars in enumerate(lengths, start=1):
        if chars < MIN_PAGE_CHARS:
            violations.append(
                f"page {index} of {total} is effectively blank: {chars} "
                f"characters of extractable text, floor is {MIN_PAGE_CHARS}"
            )
    return violations


def describe_violations(violations: list[str]) -> str:
    """A repair instruction naming the measurements, so a retry can act on it.

    Same contract as `composition_policy.describe_violations`: the numbers are
    the message. Restating "it must be two pages" to a model that already
    produced three is what this whole check replaces.
    """
    if not violations:
        return ""
    return (
        "The compiled PDF breaks the page rules: " + "; ".join(violations)
        + ". A blank or near-empty page means a page break fired with no "
          "content behind it — remove the break or supply the section it was "
          "protecting. Adjust the amount of content to reach the required page "
          "count; do not pad."
    )


def log_violations(violations: list[str], label: str = "") -> None:
    """Log at error level, or say nothing at all.

    Error, not warning, and deliberately no separate "checked, all good" line:
    a log that reads the same whether or not the check ran is the failure mode
    `utils/pdf_validator.py` is stuck in.
    """
    if not violations:
        return
    logger.error("[pages] %s: %s", label or "document", "; ".join(violations))
