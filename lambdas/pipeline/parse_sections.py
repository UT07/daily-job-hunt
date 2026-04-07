"""Parse LaTeX resumes and cover letters into editable plain-text sections.

Provides four public functions:
- parse_resume_sections(tex_content) -> dict
- rebuild_tex_from_sections(sections, base_tex) -> str
- parse_cover_letter_sections(tex_content) -> dict
- analyze_sections_vs_jd(sections, jd) -> dict

The parsed structure is a plain-text dict that the Smart Section Editor
(Phase 3.3b) uses to render editable fields in the frontend.
"""

import re
import sys
import os
from pathlib import Path

# Allow imports from the project root when running from Lambda or locally.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_latex(text: str) -> str:
    """Remove LaTeX markup from text and return clean readable content.

    Handles the common patterns found in this resume template:
    - \\textbf{...}, \\textit{...}, \\emph{...} -> inner text
    - \\href{url}{text} -> text only
    - \\item -> stripped (leading dash handled by caller)
    - \\hfill -> whitespace
    - Isolated command tokens like \\textbar\\ -> " | "
    - Remaining \\cmd -> removed
    """
    if not text:
        return ""

    # \\[...] vertical spacing FIRST (e.g. \\[0.08em]) — must run before other replacements
    # In a Python string the LaTeX token \\ = two chars, followed by [spacing]
    text = re.sub(r"\\\\(\[[^\]]*\])?", " ", text)

    # LaTeX special character escapes -> plain text equivalents
    text = text.replace(r"\&", "&")
    text = text.replace(r"\%", "%")
    text = text.replace(r"\#", "#")
    text = text.replace(r"\_", "_")
    text = text.replace(r"\,", " ")  # thin space (e.g. Route\,53)

    # \\textbar\\ (pipe separator used in header)
    text = re.sub(r"\\textbar\\?", " | ", text)

    # \\href{url}{display} -> display text
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)

    # \\textbf{...}, \\textit{...}, \\emph{...}, \\small{...}, etc.
    for cmd in ("textbf", "textit", "emph", "small", "normalsize",
                "Large", "large", "footnotesize", "texttt", "normalfont",
                "textsc", "textsf", "textrm"):
        text = re.sub(rf"\\{cmd}\{{([^}}]*)\}}", r"\1", text)

    # \\hfill -> single space
    text = re.sub(r"\\hfill\b", " ", text)

    # \\item -> empty (bullet marker, handled by caller)
    text = re.sub(r"\\item\b", "", text)

    # Remaining \\command (optionally with trailing \\) -> empty
    text = re.sub(r"\\[a-zA-Z]+\*?\\?", "", text)

    # Remove lone braces
    text = re.sub(r"[{}]", "", text)

    # Condense whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_section_body(body: str, section_name: str) -> str:
    """Return the raw LaTeX content between \\section*{section_name} and the
    next \\section* (or end of document)."""
    pattern = re.compile(
        r"\\section\*\{" + re.escape(section_name) + r"\}(.*?)(?=\\section\*\{|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(body)
    return m.group(1).strip() if m else ""


def _balanced_arg(tex: str, start: int) -> tuple[str, int]:
    """Extract one balanced {…} argument starting at `start` (must point to '{').
    Returns (content, index_after_closing_brace)."""
    if start >= len(tex) or tex[start] != "{":
        return "", start
    depth = 0
    i = start
    while i < len(tex):
        ch = tex[i]
        if ch == "\\" and i + 1 < len(tex):
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return tex[start + 1:i], i + 1
        i += 1
    return tex[start + 1:], len(tex)


def _extract_macro_args(tex: str, match_end: int, n_args: int) -> tuple[list[str], int]:
    """Extract `n_args` consecutive balanced {…} arguments from `match_end`.
    Returns (list_of_arg_strings, position_after_last_arg)."""
    args = []
    pos = match_end
    for _ in range(n_args):
        # Skip whitespace
        while pos < len(tex) and tex[pos].isspace():
            pos += 1
        if pos >= len(tex) or tex[pos] != "{":
            break
        arg, pos = _balanced_arg(tex, pos)
        args.append(arg)
    return args, pos


def _extract_itemize_bullets(tex: str, start: int) -> tuple[list[str], int]:
    """Extract bullet strings from \\begin{itemize}…\\end{itemize} starting at `start`.
    Returns (bullets, index_after_end_itemize)."""
    begin_marker = r"\begin{itemize}"
    end_marker = r"\end{itemize}"
    # Find the opening
    bi = tex.find(begin_marker, start)
    if bi < 0:
        return [], start
    ei = tex.find(end_marker, bi)
    if ei < 0:
        ei = len(tex)
    block = tex[bi + len(begin_marker):ei]
    # Split by \item
    parts = re.split(r"\\item\b", block)
    bullets: list[str] = []
    for part in parts:
        cleaned = _strip_latex(part).strip()
        if cleaned:
            bullets.append(cleaned)
    return bullets, ei + len(end_marker)


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

def _parse_header(body: str) -> dict:
    """Extract name, title, and contact info from the \\begin{center}…\\end{center} block."""
    m = re.search(r"\\begin\{center\}(.*?)\\end\{center\}", body, re.DOTALL)
    if not m:
        return {"name": "", "title": "", "contact": ""}

    raw = m.group(1)

    # Name: inside {\Large \textbf{...}}
    name_m = re.search(r"\\Large\s+\\textbf\{([^}]*)\}", raw)
    name = name_m.group(1).strip() if name_m else ""

    # Title: inside {\normalsize ...} or second line
    title_m = re.search(r"\\normalsize\s+(.*?)(?:\\\\|\n)", raw, re.DOTALL)
    if title_m:
        title = _strip_latex(title_m.group(1))
    else:
        title = ""

    # Contact: lines 3+ of the center block (after the name and title lines)
    # Remove the {\Large...} name block and {\normalsize...}\\[...] title line
    contact_raw = re.sub(r"\{\\Large[^}]*\\textbf\{[^}]*\}\}", "", raw, flags=re.DOTALL)
    contact_raw = re.sub(r"\{\\normalsize.*?(?:\\\\|\n)", "", contact_raw, flags=re.DOTALL)
    contact = _strip_latex(contact_raw).replace("\n", " ")
    # Compress multiple spaces/pipes
    contact = re.sub(r"\s*\|\s*", " | ", contact)
    contact = re.sub(r"\s{2,}", " ", contact).strip()
    # Strip any leading/trailing pipes or stray chars
    contact = contact.strip("| ").strip()

    return {"name": name, "title": title, "contact": contact}


# ---------------------------------------------------------------------------
# Summary parsing
# ---------------------------------------------------------------------------

def _parse_summary(body: str) -> str:
    raw = _extract_section_body(body, "Summary")
    return _strip_latex(raw)


# ---------------------------------------------------------------------------
# Skills parsing
# ---------------------------------------------------------------------------

def _parse_skills(body: str) -> list[dict]:
    """Extract skills as [{"category": "...", "items": "..."}].

    Each \\item \\textbf{Category:} items line becomes one entry.
    """
    raw = _extract_section_body(body, "Technical Skills")
    if not raw:
        return []

    # Find the itemize block
    m = re.search(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", raw, re.DOTALL)
    if not m:
        return []

    items_block = m.group(1)
    parts = re.split(r"\\item\b", items_block)
    skills: list[dict] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Category is in \textbf{Category:}
        cat_m = re.match(r"\\textbf\{([^}]*)\}\s*(.*)", part, re.DOTALL)
        if cat_m:
            category = cat_m.group(1).rstrip(":").strip()
            items_raw = cat_m.group(2)
        else:
            # No bold category — treat whole line as items, no category label
            category = ""
            items_raw = part
        items_clean = _strip_latex(items_raw).replace("\n", " ").strip()
        items_clean = re.sub(r"\s{2,}", " ", items_clean)
        if items_clean:
            skills.append({"category": category, "items": items_clean})
    return skills


# ---------------------------------------------------------------------------
# Experience parsing
# ---------------------------------------------------------------------------

def _parse_experience(body: str) -> list[dict]:
    """Extract experience entries from \\jobentry{company}{location}{dates}{title}
    followed by an \\begin{itemize}…\\end{itemize} block."""
    raw = _extract_section_body(body, "Experience")
    if not raw:
        return []

    entries: list[dict] = []
    pattern = re.compile(r"\\jobentry(?![a-zA-Z])")
    for m in pattern.finditer(raw):
        args, after_args = _extract_macro_args(raw, m.end(), 4)
        if len(args) < 4:
            continue
        company = _strip_latex(args[0])
        # location = args[1] (unused in structured output but we keep it accessible)
        dates = _strip_latex(args[2])
        title = _strip_latex(args[3])

        bullets, _ = _extract_itemize_bullets(raw, after_args)
        entries.append({
            "company": company,
            "title": title,
            "dates": dates,
            "bullets": bullets,
        })
    return entries


# ---------------------------------------------------------------------------
# Projects parsing
# ---------------------------------------------------------------------------

def _parse_projects(body: str) -> list[dict]:
    """Extract project entries from \\projectentryurl (5 args) or \\projectentry (3 args)."""
    raw = _extract_section_body(body, "Featured Projects")
    if not raw:
        return []

    entries: list[dict] = []

    # Combined pattern — find all project macros in order
    pattern = re.compile(r"\\(projectentryurl|projectentry)(?![a-zA-Z])")
    for m in pattern.finditer(raw):
        macro_name = m.group(1)
        n_args = 5 if macro_name == "projectentryurl" else 3
        args, after_args = _extract_macro_args(raw, m.end(), n_args)
        if len(args) < n_args:
            continue

        name = _strip_latex(args[0])
        dates = _strip_latex(args[1])
        if macro_name == "projectentryurl":
            # args: name, dates, url, url_text, tech
            tech = _strip_latex(args[4])
        else:
            # args: name, dates, tech
            tech = _strip_latex(args[2])

        bullets, _ = _extract_itemize_bullets(raw, after_args)
        entries.append({
            "name": name,
            "dates": dates,
            "tech": tech,
            "bullets": bullets,
        })
    return entries


# ---------------------------------------------------------------------------
# Education parsing
# ---------------------------------------------------------------------------

def _parse_education(body: str) -> list[dict]:
    """Extract education entries.

    Each entry uses the pattern:
      \\textbf{School}, location \\hfill \\textit{dates}\\\\[-...]
      \\textbf{\\textit{Degree}}
    """
    raw = _extract_section_body(body, "Education")
    if not raw:
        return []

    # Split by \textbf{...} institution lines — lines that are followed by dates
    # Pattern: line starting with \textbf{School Name}
    entries: list[dict] = []

    # Each institution block: a \textbf line containing school info + \hfill dates
    # Then a degree line on the next line
    block_pattern = re.compile(
        r"\\textbf\{([^}]+)\}([^\n]*?)\\hfill\s*\\textit\{([^}]+)\}"
        r"[^\n]*\n\s*\\textbf\{\\textit\{([^}]+)\}|\\textbf\{([^}]+)\}",
        re.MULTILINE,
    )

    for m in block_pattern.finditer(raw):
        school = _strip_latex(m.group(1)).strip(", ")
        dates = _strip_latex(m.group(3)).strip()
        degree_raw = m.group(4) or m.group(5) or ""
        degree = _strip_latex(degree_raw).strip()
        if school:
            entries.append({"school": school, "degree": degree, "dates": dates})

    return entries


# ---------------------------------------------------------------------------
# Certifications parsing
# ---------------------------------------------------------------------------

def _parse_certifications(body: str) -> list[dict]:
    """Extract certifications from the \\begin{itemize} block under Certifications.

    Each \\item has the form: \\href{url}{\\textbf{\\textit{Name}}} \\hfill \\textit{date}
    """
    raw = _extract_section_body(body, "Certifications")
    if not raw:
        return []

    m = re.search(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", raw, re.DOTALL)
    if not m:
        return []

    certs: list[dict] = []
    parts = re.split(r"\\item\b", m.group(1))
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Extract date: \textit{Issued ...} after \hfill
        date_m = re.search(r"\\hfill\s*\\textit\{([^}]+)\}", part)
        date_str = date_m.group(1).strip() if date_m else ""
        # Strip date part and get cert name
        name_raw = part
        if date_m:
            name_raw = part[: date_m.start()]
        name = _strip_latex(name_raw).strip()
        # Clean up "Issued " prefix from date
        date_str = re.sub(r"^Issued\s+", "", date_str).strip()
        if name:
            certs.append({"name": name, "date": date_str})
    return certs


# ---------------------------------------------------------------------------
# Public: parse_resume_sections
# ---------------------------------------------------------------------------

def parse_resume_sections(tex_content: str) -> dict:
    """Parse a LaTeX resume into a structured plain-text dict.

    Args:
        tex_content: Full .tex content including preamble and \\begin{document}.

    Returns:
        dict with keys: header, summary, skills, experience, projects,
        education, certifications.
    """
    # Extract the document body (between \begin{document} and \end{document})
    begin_marker = r"\begin{document}"
    end_marker = r"\end{document}"
    bi = tex_content.find(begin_marker)
    if bi >= 0:
        ei = tex_content.rfind(end_marker)
        body = tex_content[bi + len(begin_marker) : ei if ei > bi else len(tex_content)]
    else:
        body = tex_content

    return {
        "header": _parse_header(body),
        "summary": _parse_summary(body),
        "skills": _parse_skills(body),
        "experience": _parse_experience(body),
        "projects": _parse_projects(body),
        "education": _parse_education(body),
        "certifications": _parse_certifications(body),
    }


# ---------------------------------------------------------------------------
# Rebuild helpers
# ---------------------------------------------------------------------------

def _escape_tex(text: str) -> str:
    """Escape characters that are special in LaTeX (ampersand, percent, hash, etc.)."""
    text = text.replace("&", r"\&")
    text = text.replace("%", r"\%")
    text = text.replace("#", r"\#")
    # Do not escape underscores in URLs — callers handle that separately
    return text


def _bullets_to_itemize(bullets: list[str]) -> str:
    """Convert a list of plain-text bullet strings to a LaTeX itemize block."""
    if not bullets:
        return ""
    items = "\n".join(f"  \\item {_escape_tex(b)}" for b in bullets)
    return f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}"


def _rebuild_header(header: dict) -> str:
    name = header.get("name", "")
    title = header.get("title", "")
    contact_raw = header.get("contact", "")

    # Split contact on " | " to get individual parts
    contact_parts = [p.strip() for p in contact_raw.split(" | ") if p.strip()]
    # Re-join with LaTeX separator
    contact_line = r" \textbar\ ".join(contact_parts)

    return (
        r"\begin{center}" + "\n"
        r"{" r"\Large \textbf{" + name + r"}}" + r"\\[0.04em]" + "\n"
        r"{\normalsize " + _escape_tex(title) + r"}\\[0.08em]" + "\n"
        + contact_line + "\n"
        r"\end{center}" + "\n"
        r"\vspace{0.06em}"
    )


def _rebuild_skills(skills: list[dict]) -> str:
    if not skills:
        return ""
    lines = []
    for s in skills:
        cat = s.get("category", "")
        items = s.get("items", "")
        if cat:
            lines.append(f"  \\item \\textbf{{{_escape_tex(cat)}:}} {_escape_tex(items)}")
        else:
            lines.append(f"  \\item {_escape_tex(items)}")
    items_block = "\n".join(lines)
    return f"\\begin{{itemize}}\n{items_block}\n\\end{{itemize}}"


def _rebuild_experience(experience: list[dict]) -> str:
    parts = []
    for idx, entry in enumerate(experience):
        company = _escape_tex(entry.get("company", ""))
        title = _escape_tex(entry.get("title", ""))
        dates = _escape_tex(entry.get("dates", ""))
        bullets = entry.get("bullets", [])

        # jobentry{company}{location}{dates}{title}
        # We don't store location separately in the parsed dict, so use empty string
        macro = f"\\jobentry{{{company}}}{{}}{{{dates}}}{{\\textbf{{\\textit{{{title}}}}}}}"
        block = macro + "\n" + _bullets_to_itemize(bullets)
        if idx > 0:
            block = "\n" + block
        parts.append(block)
    return "\n".join(parts)


def _rebuild_projects(projects: list[dict]) -> str:
    parts = []
    for idx, entry in enumerate(projects):
        name = _escape_tex(entry.get("name", ""))
        dates = _escape_tex(entry.get("dates", ""))
        tech = _escape_tex(entry.get("tech", ""))
        bullets = entry.get("bullets", [])

        # Use projectentry (3-arg, no URL) since we don't preserve the URL in parsed form
        macro = f"\\projectentry{{{name}}}{{{dates}}}{{{tech}}}"
        block = macro + "\n" + _bullets_to_itemize(bullets)
        if idx > 0:
            block = "\\vspace{0.10em}\n" + block
        parts.append(block)
    return "\n".join(parts)


def _rebuild_education(education: list[dict]) -> str:
    parts = []
    for idx, entry in enumerate(education):
        school = _escape_tex(entry.get("school", ""))
        degree = _escape_tex(entry.get("degree", ""))
        dates = _escape_tex(entry.get("dates", ""))
        block = (
            f"\\textbf{{{school}}} \\hfill \\textit{{{dates}}}\\\\[-0.15em]\n"
            f"\\textbf{{\\textit{{{degree}}}}}\\\\[-0.25em]"
        )
        if idx > 0:
            block = "\\vspace{0.12em}\n" + block
        parts.append(block)
    return "\n".join(parts)


def _rebuild_certifications(certifications: list[dict]) -> str:
    if not certifications:
        return ""
    lines = []
    for cert in certifications:
        name = _escape_tex(cert.get("name", ""))
        date = _escape_tex(cert.get("date", ""))
        if date:
            lines.append(f"  \\item \\textbf{{\\textit{{{name}}}}} \\hfill \\textit{{Issued {date}}}")
        else:
            lines.append(f"  \\item \\textbf{{\\textit{{{name}}}}}")
    items_block = "\n".join(lines)
    return f"\\begin{{itemize}}\n{items_block}\n\\end{{itemize}}"


# ---------------------------------------------------------------------------
# Public: rebuild_tex_from_sections
# ---------------------------------------------------------------------------

def rebuild_tex_from_sections(sections: dict, base_tex: str) -> str:
    """Reconstruct a compilable .tex string from edited sections + original base template.

    Uses the original preamble (\\documentclass, \\usepackage, custom macros)
    from `base_tex`, then injects the edited content into the document body.

    Args:
        sections: dict as returned (and potentially edited) by parse_resume_sections().
        base_tex: Original .tex content — used only for the preamble.

    Returns:
        Compilable .tex string.
    """
    # Extract preamble from base (everything before \begin{document})
    begin_marker = r"\begin{document}"
    bi = base_tex.find(begin_marker)
    if bi >= 0:
        preamble = base_tex[:bi].rstrip()
    else:
        # No \begin{document} found — generate a minimal preamble
        preamble = (
            r"\documentclass[10pt,a4paper]{article}" + "\n"
            r"\usepackage[utf8]{inputenc}" + "\n"
            r"\usepackage[T1]{fontenc}" + "\n"
        )

    header = sections.get("header", {})
    summary = sections.get("summary", "")
    skills = sections.get("skills", [])
    experience = sections.get("experience", [])
    projects = sections.get("projects", [])
    education = sections.get("education", [])
    certifications = sections.get("certifications", [])

    body_parts = [
        "%==================== HEADER ====================",
        _rebuild_header(header),
        "",
        "%==================== SUMMARY ====================",
        r"\section*{Summary}",
        _escape_tex(summary),
        "",
        "%==================== TECHNICAL SKILLS ====================",
        r"\section*{Technical Skills}",
        _rebuild_skills(skills),
        "",
        "%==================== EXPERIENCE ====================",
        r"\section*{Experience}",
        _rebuild_experience(experience),
        "",
        "%==================== PROJECTS ====================",
        r"\section*{Featured Projects}",
        _rebuild_projects(projects),
        "",
        "%==================== EDUCATION ====================",
        r"\section*{Education}",
        _rebuild_education(education),
        "",
        "%==================== CERTIFICATIONS ====================",
        r"\section*{Certifications}",
        _rebuild_certifications(certifications),
    ]

    body = "\n".join(body_parts)
    return f"{preamble}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


# ---------------------------------------------------------------------------
# Public: parse_cover_letter_sections
# ---------------------------------------------------------------------------

def parse_cover_letter_sections(tex_content: str) -> dict:
    """Parse a LaTeX cover letter into editable plain-text sections.

    Extracted sections: greeting, opening, body1, body2, closing.

    Args:
        tex_content: Full .tex cover letter content.

    Returns:
        dict with keys: greeting, opening, body1, body2, closing.
    """
    begin_marker = r"\begin{document}"
    end_marker = r"\end{document}"
    bi = tex_content.find(begin_marker)
    if bi >= 0:
        ei = tex_content.rfind(end_marker)
        body = tex_content[bi + len(begin_marker): ei if ei > bi else len(tex_content)]
    else:
        body = tex_content

    # Strip the header block (\begin{center}...\end{center})
    body_no_header = re.sub(r"\\begin\{center\}.*?\\end\{center\}", "", body, flags=re.DOTALL)

    # Strip preamble decorations (\vspace, \hrule, \today, Re: ...)
    body_no_header = re.sub(r"\\vspace\{[^}]*\}", "", body_no_header)
    body_no_header = re.sub(r"\\hrule\b[^\n]*", "", body_no_header)
    body_no_header = re.sub(r"\\today\b", "", body_no_header)
    body_no_header = re.sub(r"\\hspace\{[^}]*\}", "", body_no_header)

    # Strip all remaining LaTeX commands for plain text splitting
    plain = _strip_latex(body_no_header)

    # Split into paragraphs (blank lines between paragraphs)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", plain) if p.strip()]

    # Heuristic mapping of paragraphs to sections:
    # typical structure: [date, greeting_line, Re: line, para1, para2, para3, closing, signature]
    # We look for the "Hiring Team" or "Dear" line as greeting, then take up to 3 body paragraphs
    greeting = ""
    opening = ""
    body1 = ""
    body2 = ""
    closing = ""

    body_paragraphs = []
    for p in paragraphs:
        p_lower = p.lower()
        if not greeting and ("hiring team" in p_lower or "dear" in p_lower or p_lower.startswith("re:")):
            greeting = p
        elif not opening and len(p.split()) > 15:
            # First substantial paragraph = opening
            opening = p
        elif len(body_paragraphs) < 2 and len(p.split()) > 10:
            body_paragraphs.append(p)
        elif (
            not closing
            and len(p.split()) < 15
            and ("regards" in p_lower or "sincerely" in p_lower or "thank" in p_lower)
        ):
            closing = p

    body1 = body_paragraphs[0] if body_paragraphs else ""
    body2 = body_paragraphs[1] if len(body_paragraphs) > 1 else ""

    return {
        "greeting": greeting,
        "opening": opening,
        "body1": body1,
        "body2": body2,
        "closing": closing,
    }


# ---------------------------------------------------------------------------
# Public: analyze_sections_vs_jd
# ---------------------------------------------------------------------------

def analyze_sections_vs_jd(sections: dict, jd: str) -> dict:
    """Compare resume sections against a job description and compute coverage.

    Args:
        sections: dict as returned by parse_resume_sections().
        jd: Job description plain text.

    Returns:
        dict mapping section names to:
            {keywords_matched, keywords_missing, coverage_score (0-100)}
        Plus a top-level "jd_keywords" list.
    """
    from utils.keyword_extractor import extract_keywords

    if not jd:
        return {}

    jd_keywords = extract_keywords(jd, max_keywords=20)
    if not jd_keywords:
        return {}

    def _section_text(section_key: str) -> str:
        """Flatten a section value to a single lowercase string."""
        val = sections.get(section_key, "")
        if isinstance(val, str):
            return val.lower()
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict):
                    parts.extend(str(v) for v in item.values())
                else:
                    parts.append(str(item))
            return " ".join(parts).lower()
        if isinstance(val, dict):
            return " ".join(str(v) for v in val.values()).lower()
        return ""

    analysis: dict[str, dict] = {}
    section_keys = ["summary", "skills", "experience", "projects", "education", "certifications"]

    for key in section_keys:
        text = _section_text(key)
        matched = []
        missing = []
        for kw in jd_keywords:
            if kw.lower() in text:
                matched.append(kw)
            else:
                missing.append(kw)
        coverage = round(len(matched) / len(jd_keywords) * 100) if jd_keywords else 0
        analysis[key] = {
            "keywords_matched": matched,
            "keywords_missing": missing,
            "coverage_score": coverage,
        }

    # Overall coverage across all sections
    all_matched = set()
    for key in section_keys:
        all_matched.update(kw.lower() for kw in analysis[key]["keywords_matched"])
    overall = round(len(all_matched) / len(jd_keywords) * 100) if jd_keywords else 0

    return {
        "jd_keywords": jd_keywords,
        "sections": analysis,
        "overall_coverage": overall,
    }
