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
from typing import NamedTuple

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


class BaseResume(NamedTuple):
    """What every generator must agree on, plus enough to explain a refusal."""

    row: dict | None
    skipped: int
    newest_tex: str | None
    n_rows: int = 0
    all_tex: str = ""
    """Every row's tex_content joined -- the fabrication baseline.

    NOT the same question as `row`. `row` is the document to tailor, and only
    one can be. Whether the candidate has ever claimed a skill is a question
    about the whole profile, and answering it from one row convicts them of
    content an earlier row had and the current one dropped.

    Measured on 140 real resumes, with word-boundary matching:
        baseline = production row only    97/140 flagged (69.3%), java alone 88
        baseline = union of all rows      23/140 flagged (16.4%)
    The 74-resume difference is entirely Java, which the 2026-04-05 row lists
    and the 2026-09-28 row does not.
    """

    @property
    def tex(self) -> str:
        """Empty string, never None -- callers concatenate this into prompts."""
        return (self.row or {}).get("tex_content", "") or ""

    def why_unusable(self, user_id: str = "") -> str:
        """Message for the person who uploaded it, when `row` is None.

        `n_rows` is carried rather than inferred from `newest_tex`, because
        "no resume has ever been uploaded" and "the newest upload produced no
        text" are two different operator situations with two different
        remedies, and describe_why_not_latex(None) answers the second for
        both -- it returns "the file produced no text", which is actively
        misleading when there is no file. A test caught this: the truthy
        string meant the no-rows branch could never be reached.
        """
        if self.n_rows == 0:
            return f"no resume found for user {user_id}" if user_id else "no resume found"
        return describe_why_not_latex(self.newest_tex) or "it is not a LaTeX document"


def fetch_tailorable_resume(db, user_id: str, *, limit: int = 10) -> BaseResume:
    """The base resume every generator must agree on. One rule, one place.

    `pick_latest_tailorable` was added after the 2026-09-28 incident above and
    wired into tailor_resume.py only. score_batch.py and
    generate_cover_letter.py kept their own read -- `.limit(1)` with no
    validity check at all -- so the repository held THREE different rules for
    "which resume is the user's":

        tailor_resume.py:543           limit(10) + pick_latest_tailorable
        score_batch.py:203             limit(1),  no check
        generate_cover_letter.py:217   limit(1),  no check

    They agree today only because both of this user's two rows happen to be
    valid LaTeX. The failure is not really about LaTeX though -- it is
    DIVERGENCE. One non-LaTeX upload becomes the newest row, and from that
    moment tailoring uses the older LaTeX document while scoring and the cover
    letter use the newer extracted text. The system then scores, and writes a
    letter about, a different document than the one it sends to the employer,
    and every layer reports success. That is CLAUDE.md rule 2, and rule 10: the
    guard existed, the data existed, and they met in one place out of three.

    So all three read through here. If nothing is tailorable the caller gets
    `row=None` and fails the way it already fails for "no resume" -- loud and
    consistent -- rather than quietly generating from a document the tailorer
    refuses. `skipped` is returned so every caller can log that a newer resume
    was passed over; "why is it using my old one?" should have an answer in the
    logs at any of the three, not only one. `newest_tex` is carried so a
    refusal can say WHAT was wrong with the upload rather than just that
    something was.
    """
    rows = (
        db.table("user_resumes").select("*").eq("user_id", user_id)
        .order("created_at", desc=True).limit(limit).execute().data
    ) or []
    row, skipped = pick_latest_tailorable(rows)
    newest_tex = rows[0].get("tex_content") if rows else None
    return BaseResume(
        row=row, skipped=skipped, newest_tex=newest_tex, n_rows=len(rows),
        all_tex="\n".join(r.get("tex_content") or "" for r in rows),
    )


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

    # github/linkedin/website are joined here, not dropped. Measured on a real
    # master PDF 2026-09-30: segment_sections captures both links in the
    # `_header` block (99.6% of the document is segmented), the parser now asks
    # for them, and this join is the last place they could be lost. A resume
    # without the candidate's GitHub is materially worse -- it is how a reviewer
    # verifies the projects listed two sections below.
    contact = " | ".join(
        str(parsed.get(k) or "").strip()
        for k in ("email", "phone", "location", "github", "linkedin", "website")
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
            # Carried so the renderer can emit \projectentryurl instead of the
            # 3-arg \projectentry. parse_sections._parse_projects has always
            # been able to READ a 5-arg entry; nothing ever produced one from a
            # PDF, so every project link was dropped on upload.
            "url": str(entry.get("url") or "").strip(),
            "tech": entry.get("tech", ""),
            "bullets": _bullets(entry.get("bullets")),
        })

    certifications = []
    for entry in parsed.get("certifications") or []:
        if isinstance(entry, dict):
            certifications.append(entry)
        elif str(entry).strip():
            certifications.append({"name": str(entry).strip(), "date": ""})

    # Lines that sit beside the certifications without being certifications:
    # spoken languages, work authorisation, availability. Real resumes park
    # them under an "Additional" heading in the same block, and segmentation
    # puts them in `certifications` -- so before this they reached the parser
    # and were discarded by a prompt that only asked for certification names.
    # On the measured master that silently dropped three languages AND
    # "Right to work: Stamp 1G", which is the single most load-bearing fact on
    # the document for an Irish job search.
    additional = [
        str(line).strip() for line in (parsed.get("additional") or [])
        if str(line).strip()
    ]

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
        # Passed through whole, so `coursework` reaches the renderer. That field
        # is why a real master PDF was REFUSED before: the education section
        # lists named modules ("Cloud Architectures, Cloud DevOpsSec, Scalable
        # Cloud Programming, ...") that the schema never captured, so
        # section_recall measured education at 74% against an 88% floor and
        # conversion_is_faithful rejected the upload -- leaving the user's old
        # resume in place with no visible reason.
        "education": [e for e in (parsed.get("education") or []) if isinstance(e, dict)],
        "additional": additional,
        "certifications": certifications,
    }
