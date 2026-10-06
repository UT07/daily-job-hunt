"""Does our own PDF survive the way an ATS reads it?

Nothing asked this. The pipeline validates the LaTeX source (brace balance,
required sections, macro arity) and the compiled PDF's page count, and then
ships a document whose entire purpose is to be parsed by software nobody here
has ever pointed at it. A resume can be structurally perfect in source and
still arrive at Workday as interleaved columns or a page of nothing.

Workday, Greenhouse, Taleo and iCIMS extract text with layout engines close to
pdfminer/pdfplumber — the same library already in this repo's requirements for
`shared/page_check`. So the check is to read our own output the way they do and
assert the things an ATS needs:

    1. every section is FINDABLE in the extracted text
    2. the sections come out in the SAME ORDER as the source
    3. the dates survive as dates
    4. there is actually text, rather than a picture of text

(2) is the one that matters most and is least obvious. A two-column resume
reads perfectly to a human and extracts as alternating fragments of both
columns, which is why "it looks fine" is not evidence. This layout is
single-column, so the check should pass today — a check whose first run is
green is still worth having when it is cheap, deterministic, and guards a
property a future template change could silently break.

Returns a list of violations, empty meaning measured and compliant. Same
contract as shared/page_check, deliberately: these are two answers to "is the
artefact we produced actually usable", and they should read the same way.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised by monkeypatching the name to None
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None

_SECTION = re.compile(r"\\section\*?\{([^}]*)\}")
_TEX_COMMAND = re.compile(r"\\[a-zA-Z]+\*?\s*")

# "Jun 2026 - Present", "2024 - Present", "Sep 2020 - May 2022". Matches what
# the corpus emits after shared/fit_to_pages normalises separators to hyphens.
_DATE_RANGE = re.compile(
    r"((?:[A-Z][a-z]{2,8}\.?\s+)?\d{4})\s*[-\u2010-\u2015]\s*"
    r"((?:[A-Z][a-z]{2,8}\.?\s+)?\d{4}|Present|Current|Now)"
)

# Below this, the "PDF" is a picture of a resume or an empty one. A real
# two-page resume measures ~8,000 extractable characters; 500 is far enough
# below that to mean something is wrong rather than merely terse.
MIN_EXTRACTED_CHARS = 500


def _norm(text: str) -> str:
    """Comparable form: no LaTeX, no case, no runs of whitespace."""
    text = _TEX_COMMAND.sub(" ", text or "")
    text = re.sub(r"[{}]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def extract_text(pdf_path: str | os.PathLike) -> str:
    """The document as an ATS would read it: pages in order, text only."""
    if pdfplumber is None:  # pragma: no cover
        raise RuntimeError("pdfplumber is not installed")
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def check_ats_extraction(pdf_path: str | os.PathLike, tex: str) -> list[str]:
    """Violations an ATS would hit. Empty means measured and compliant."""
    if pdfplumber is None:  # pragma: no cover
        # Reports a violation rather than returning success, for the reason
        # page_check does: a checker that cannot run must not look like a pass.
        return ["ats: pdfplumber is not installed, so extraction was not checked"]

    try:
        extracted = extract_text(pdf_path)
    except Exception as exc:  # noqa: BLE001
        return [f"ats: could not extract text ({exc})"]

    violations: list[str] = []
    flat = _norm(extracted)

    dense = len(re.sub(r"\s", "", extracted))
    if dense < MIN_EXTRACTED_CHARS:
        violations.append(
            f"ats: only {dense} characters extracted (floor "
            f"{MIN_EXTRACTED_CHARS}) — the text may be an image"
        )
        return violations  # everything below would pile on from one cause

    sections = [_norm(m.group(1)) for m in _SECTION.finditer(tex or "")]
    sections = [s for s in sections if s]

    missing = [s for s in sections if s not in flat]
    if missing:
        violations.append(
            f"ats: section(s) not findable in the extracted text: {missing}")

    # Reading order. Only over the sections that WERE found, so a missing
    # section is reported once as missing rather than again as out of order.
    found = [s for s in sections if s in flat]
    positions = [flat.index(s) for s in found]
    if positions != sorted(positions):
        out_of_order = [s for s, _ in sorted(zip(found, positions), key=lambda p: p[1])]
        violations.append(
            f"ats: sections extract out of order — an ATS would read them as "
            f"{out_of_order}, the document says {found}. This is what a "
            f"multi-column layout does."
        )

    tex_dates = {(_norm(a), _norm(b)) for a, b in _DATE_RANGE.findall(tex or "")}
    pdf_dates = {(_norm(a), _norm(b)) for a, b in _DATE_RANGE.findall(extracted)}
    lost = tex_dates - pdf_dates
    if lost:
        violations.append(
            f"ats: {len(lost)} date range(s) did not survive extraction, e.g. "
            f"{sorted(lost)[0][0]} - {sorted(lost)[0][1]} — employment dates an "
            f"ATS cannot parse become gaps on the candidate's timeline"
        )

    return violations


def log_violations(violations: list[str], label: str = "") -> None:
    """Same shape as page_check.log_violations, so both read alike in logs."""
    for v in violations:
        logger.warning("[ats] %s%s", f"{label}: " if label else "", v)
