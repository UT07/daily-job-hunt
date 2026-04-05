"""PDF output validation for compiled resumes and cover letters."""
from __future__ import annotations

from pathlib import Path

try:
    import fitz  # pymupdf
except ImportError:
    fitz = None

REQUIRED_SECTIONS = ["skills", "experience"]


def check_file_size(size_bytes: int) -> str | None:
    """Return an issue string if size is outside expected bounds, else None."""
    if size_bytes < 10_000:
        return "too_small"
    if size_bytes > 500_000:
        return "too_large"
    return None


def validate_pdf(
    pdf_path: str,
    expected_pages: int = 2,
    check_sections: bool = True,
) -> dict:
    """Validate a compiled PDF.

    Checks performed:
    - File existence
    - File size (10KB-500KB expected range)
    - Page count matches expectation
    - Text extraction yields meaningful content (>100 chars)
    - Required sections present (skills, experience)
    - Last page doesn't end mid-sentence (content overflow warning)

    Returns dict with keys: valid (bool), errors (list[str]), warnings (list[str]).
    """
    errors: list[str] = []
    warnings: list[str] = []
    path = Path(pdf_path)

    if not path.exists():
        return {"valid": False, "errors": ["file_not_found"], "warnings": []}

    size = path.stat().st_size
    size_issue = check_file_size(size)
    if size_issue:
        errors.append(f"file_size: {size_issue} ({size} bytes)")

    if fitz is None:
        warnings.append("pymupdf not installed — skipping content validation")
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    doc = fitz.open(str(path))

    if len(doc) != expected_pages:
        errors.append(f"page_count: {len(doc)} (expected {expected_pages})")

    full_text = ""
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        full_text += page.get_text().lower()

    if len(full_text.strip()) < 100:
        errors.append("text_extraction: less than 100 chars extracted")

    if check_sections:
        for section in REQUIRED_SECTIONS:
            if section not in full_text:
                warnings.append(f"missing_section: '{section}' not found")

    if len(doc) >= expected_pages:
        last_page = doc.load_page(len(doc) - 1)
        last_text = last_page.get_text().strip()
        if last_text and last_text[-1] not in '.!?)"\'':
            warnings.append("content_overflow: last page may end mid-sentence")

    doc.close()
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
