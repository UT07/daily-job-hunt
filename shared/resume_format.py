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


def adapt_parsed_resume_sections(parsed: dict) -> dict:
    r"""Reshape resume_parser output into what rebuild_tex_from_sections wants.

    Two different parsers feed the same renderer and they disagree on shape:

        resume_parser (PDF text -> AI)      parse_sections (LaTeX -> dict)
        ----------------------------       ------------------------------
        name/title_line/email/phone        header: {name, title, contact}
        skills: ["Cloud: AWS, GCP"]        skills: [{category, items}]
        experience[].role                  experience[].title
        experience[].bullets: "• a\n• b"   experience[].bullets: ["a", "b"]
        certifications: ["AWS SA Pro"]     certifications: [{name, date}]
        education: [{school,degree,dates}] education: [{school,degree,dates}]

    Only `education` already lined up. Everything else raised on render — in
    practice ``'str' object has no attribute 'get'`` — which _sections_to_tex
    caught and turned into "", so the upload silently stored extracted plain
    text instead of LaTeX. That text is not tailorable, so the pipeline skipped
    it and kept using an older resume. Measured 2026-09-29: a master uploaded
    on 2026-09-28 was stored as 16,953 chars of plain text and every resume
    generated afterwards came from a 2026-04-05 template.

    Idempotent: given parse_sections-shaped input it returns it unchanged, so
    it is safe to apply on either path.
    """
    if "header" in parsed:
        return parsed  # already the renderer's shape

    contact = " | ".join(
        str(parsed.get(k) or "").strip()
        for k in ("email", "phone", "location")
        if str(parsed.get(k) or "").strip()
    )

    skills = []
    for entry in parsed.get("skills") or []:
        if isinstance(entry, dict):
            skills.append(entry)
            continue
        text = str(entry)
        # "Cloud: AWS, GCP" -> category/items. A line with no colon keeps its
        # text as items rather than being dropped, which would silently delete
        # a row from the rendered resume.
        category, sep, items = text.partition(":")
        skills.append({"category": category.strip(), "items": items.strip()}
                      if sep else {"category": "", "items": text.strip()})

    def _bullets(value):
        if isinstance(value, list):
            return [str(b).strip() for b in value if str(b).strip()]
        out = []
        for line in str(value or "").replace("•", "\n").splitlines():
            line = line.strip().lstrip("-*–— ").strip()
            if line:
                out.append(line)
        return out

    experience = []
    for entry in parsed.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        experience.append({
            "company": entry.get("company", ""),
            # resume_parser calls it `role`; accept `title` too so this stays
            # correct if that parser is ever aligned.
            "title": entry.get("title") or entry.get("role", ""),
            "dates": entry.get("dates", ""),
            "bullets": _bullets(entry.get("bullets")),
        })

    projects = []
    for entry in parsed.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        projects.append({
            "name": entry.get("name") or entry.get("title", ""),
            "dates": entry.get("dates", ""),
            "bullets": _bullets(entry.get("bullets")),
        })

    certifications = []
    for entry in parsed.get("certifications") or []:
        if isinstance(entry, dict):
            certifications.append(entry)
        elif str(entry).strip():
            certifications.append({"name": str(entry).strip(), "date": ""})

    return {
        "header": {
            "name": parsed.get("name", ""),
            "title": parsed.get("title_line") or parsed.get("title", ""),
            "contact": contact,
        },
        "summary": parsed.get("summary", ""),
        "skills": skills,
        "experience": experience,
        "projects": projects,
        "education": [e for e in (parsed.get("education") or []) if isinstance(e, dict)],
        "certifications": certifications,
    }
