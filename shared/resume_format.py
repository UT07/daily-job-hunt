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


# --- Did a conversion actually carry the content across? ---------------------

# Sections that must hold something for a converted resume to be worth storing.
# Any one of them is enough — a genuine resume has at least a summary, a skills
# list, or work history.
_SUBSTANTIVE_SECTIONS = ("summary", "skills", "experience", "projects", "education")


def sections_have_content(sections: dict | None) -> bool:
    """True when parsed sections carry more than an empty skeleton.

    is_latex_document() only checks for \\documentclass and \\begin{document},
    so a conversion that dropped every word still passes it. Measured
    2026-09-28: parse_resume_sections() with no AI client returned only
    raw_text, and rebuild_tex_from_sections turned 16,953 characters of resume
    into 1,807 characters of valid, empty LaTeX — which would have replaced a
    working resume while reporting success.

    Structural validity is not evidence of content. This is the content check.
    """
    if not sections:
        return False
    for key in _SUBSTANTIVE_SECTIONS:
        value = sections.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (list, tuple, dict)) and len(value) > 0:
            return True
    return False
