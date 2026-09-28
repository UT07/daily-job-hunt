"""Is a stored resume actually LaTeX?

`user_resumes.tex_content` is named for LaTeX and the whole tailoring pipeline
treats it as LaTeX — tailor_resume.py splits it on \\begin{document} and
raises TailorError if that marker is missing. But /api/resumes/upload extracts
text from a PDF and stores it in the same column, with the comment
"Store raw text for now".

Nothing between the two ever checked. On 2026-09-28 a PDF upload replaced a
working LaTeX base resume with plain text, and every tailoring attempt failed
with "base resume has no \\begin{document}" — surfacing in the UI four layers
later as "Regenerate failed: Pipeline failed".

Selection is newest-wins, so an upload that cannot be tailored silently
disables tailoring entirely.
"""

# Both markers, because either alone is ambiguous: a body fragment can carry
# \begin{document} with no class, and a preamble-only file has a class with no
# body. The tailorer needs both to split the document.
_REQUIRED = ("\\documentclass", "\\begin{document}")


def is_latex_document(text: str | None) -> bool:
    """True when `text` is a complete LaTeX document the tailorer can split."""
    if not text:
        return False
    return all(marker in text for marker in _REQUIRED)


def describe_why_not_latex(text: str | None) -> str:
    """A message for the person who uploaded it, not for a log."""
    if not text or not text.strip():
        return "the file produced no text"
    missing = [m for m in _REQUIRED if m not in text]
    if not missing:
        return ""
    return (
        f"it has no {' or '.join(missing)} — this looks like extracted text "
        "rather than a LaTeX source file. Tailoring needs the .tex source, "
        "because it rewrites the document body and recompiles it."
    )


def pick_latest_tailorable(rows: list[dict]) -> tuple[dict | None, int]:
    """Newest row whose tex_content is real LaTeX, and how many were skipped.

    Callers order by created_at desc. Returning the skip count lets them log
    that a newer resume was passed over, so "why is it using my old one?" has
    an answer in the logs instead of being invisible.
    """
    skipped = 0
    for row in rows or []:
        if is_latex_document(row.get("tex_content")):
            return row, skipped
        skipped += 1
    return None, skipped
