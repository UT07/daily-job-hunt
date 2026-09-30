"""Resume parser — PDF text extraction, then structured sections.

The master a user uploads is a CORPUS, not a résumé: everything about them,
three pages or ten. Its one job on ingest is to lose nothing. A résumé is
composed from it later, per job, and that is where length limits belong.

The previous version could not do that job, for two reasons that compounded:

  1. The prompt interpolated ``text[:6000]``. Measured on a real 8,490-char
     résumé PDF, 70.7% reached the model; on the 16,953-char master uploaded on
     2026-09-28, 35.4% did. Education sat at offset 7,540 and Certifications at
     8,274, so neither was ever sent — which is why "Arlington" vanished from
     the stored copy while remaining in the source.

  2. The JSON schema had no ``projects`` key at all. The string "projects" did
     not appear in the function. So the adapter's ``parsed.get("projects")``
     always returned [], the renderer emitted an empty ``\\section*{Featured
     Projects}`` header anyway, and the template's ``\\clearpage`` in front of
     it produced a blank page. Five projects became zero, structurally, with no
     truncation required.

Nothing downstream noticed: ``is_latex_document`` passed, ``sections_have_content``
passed (it needs any ONE of five sections to be non-empty), and
``check_section_completeness`` passed because it matches section header NAMES
and the renderer emits all six unconditionally. Measured end to end: an
8,490-char PDF became a compilable two-page document containing 772 characters
of content. 9.1% retention, every gate green.

The rewrite makes three changes.

**Segment first, deterministically.** ``segment_sections`` splits the extracted
text on heading lines before any model sees it. pdfplumber flattens a résumé's
formatting, so headings arrive as bare lines ("Summary", "Technical Skills",
"Experience") indistinguishable from body text by eye — but they are short,
unpunctuated, and followed by content, which is enough to find them reliably.

**One call per section.** Groq's free tier is 8k tokens/minute, so a 17k-char
corpus cannot go through the council in one request at all; removing the
truncation alone would trade silent truncation for silent provider failure.
Per-section calls stay bounded, fail independently, and let a section that
needs no interpretation skip the model entirely.

**Shapes the consumer actually wants.** ``skills`` is returned as a LIST.
The old prompt asked for prose, and ``adapt_parsed_resume_sections`` iterates
that field — so a 33-character skills string became 33 entries and rendered as
one-character bullets. ``projects`` exists. And the function always returns a
dict: valid JSON of the wrong type used to be returned as-is, so it could hand
back a list or a str against its own annotation and crash the caller.
"""

import json
import logging
from typing import Dict

logger = logging.getLogger(__name__)

# How much of one section may reach the model. Generous — a single section of a
# ten-page corpus does not approach this — but not unbounded, because one
# runaway section should not take the whole ingest down with a provider error.
# Reaching it is logged, never silent, which is the whole complaint about the
# 6,000-char cap it replaces.
MAX_SECTION_CHARS = 12_000


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes.

    pdfplumber is the only extractor that runs in any deployment: PyPDF2 is
    absent from requirements.txt, requirements-web.txt (what the API container
    installs) and layer/requirements.txt. The fallback below is dead code and
    is kept only so a local checkout with PyPDF2 installed still works.
    """
    try:
        import io
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or ""
                    text += "\n"
                return text.strip()
        except ImportError:
            pass

        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
                text += "\n"
            return text.strip()
        except ImportError:
            pass

        logger.error("No PDF library available. Install pdfplumber or PyPDF2.")
        return ""
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


# --- deterministic segmentation ---------------------------------------------

# Canonical section -> the headings that mean it. Matched case-insensitively
# against a whole line. Ordered longest-first within each group so "Technical
# Skills" wins over "Skills".
_HEADINGS = {
    "summary": ("professional summary", "executive summary", "summary",
                "profile", "objective", "about me", "about"),
    "skills": ("technical skills", "core competencies", "skills & tools",
               "technologies", "skills"),
    "experience": ("professional experience", "work experience",
                   "employment history", "experience", "employment"),
    "projects": ("featured projects", "selected projects", "personal projects",
                 "side projects", "projects"),
    "education": ("education & training", "education"),
    "certifications": ("certifications & licenses", "certifications",
                       "certificates", "licenses"),
    "awards": ("awards & honors", "awards", "honors", "achievements"),
    "publications": ("publications", "papers"),
}

# Flattened, longest first, so a longer heading is tested before its substring.
_HEADING_LOOKUP = sorted(
    ((heading, canonical) for canonical, group in _HEADINGS.items() for heading in group),
    key=lambda pair: -len(pair[0]),
)


def _heading_of(line: str) -> str | None:
    """Canonical section name if this line is a heading, else None.

    A heading is short, carries no sentence punctuation, and matches a known
    name. The length and punctuation tests matter: a bullet reading "Led the
    migration of our skills matrix." contains "skills" and must not split the
    document in half.
    """
    stripped = line.strip().strip(":").strip()
    if not stripped or len(stripped) > 40:
        return None
    if stripped.endswith((".", ",", ";")):
        return None
    lowered = stripped.lower()
    for heading, canonical in _HEADING_LOOKUP:
        if lowered == heading:
            return canonical
    return None


def segment_sections(text: str) -> Dict[str, str]:
    """Split résumé text into ``{canonical_section: body}`` plus ``_header``.

    Everything before the first recognised heading is ``_header`` — the name,
    contact details and title line. Unrecognised headings keep their own text
    under the section that precedes them rather than being dropped, so an
    unusual résumé loses nothing even when this function does not understand
    its structure.

    Deterministic and AI-free, so it costs nothing and behaves the same every
    run.
    """
    sections: Dict[str, str] = {}
    current = "_header"
    buffer: list[str] = []

    for line in (text or "").splitlines():
        canonical = _heading_of(line)
        if canonical and canonical not in sections:
            sections[current] = "\n".join(buffer).strip()
            current, buffer = canonical, []
            continue
        buffer.append(line)

    sections[current] = "\n".join(buffer).strip()
    return {name: body for name, body in sections.items() if body}


# --- per-section AI parsing --------------------------------------------------

_SYSTEM = ("You are a resume parser. Extract structured data from the text you "
           "are given. Return only valid JSON, with no markdown fences and no "
           "commentary. Never invent information that is not in the text.")

# One prompt per structured section. Each names the exact JSON shape
# adapt_parsed_resume_sections consumes, so nothing has to be reshaped later.
_SECTION_PROMPTS = {
    "_header": (
        'Return {"name": "", "email": "", "phone": "", "location": "", '
        '"title_line": "", "github": "", "linkedin": "", "website": ""} from '
        'this resume header. Use "" for anything absent. Copy any URL or handle '
        'exactly as written, without adding or removing a scheme.'
    ),
    "experience": (
        'Return {"experience": [{"company": "", "role": "", "dates": "", '
        '"bullets": ["", ""]}]} — one object per employer, IN THE ORDER GIVEN, '
        'with every bullet point. Include every employer present; do not '
        'summarise, merge or omit any.'
    ),
    "projects": (
        'Return {"projects": [{"name": "", "dates": "", "tech": "", "url": "", '
        '"bullets": ["", ""]}]} — one object per project, with every bullet. '
        'Include every project present; do not omit any. "url" is the project\'s '
        'own link if the entry shows one (a repository, demo or store listing), '
        'copied exactly and "" if there is none.'
    ),
    "education": (
        'Return {"education": [{"school": "", "degree": "", "dates": "", '
        '"coursework": ""}]} — one object per institution. Include every one '
        'present. "coursework" is everything else the entry lists -- named '
        'modules, a thesis, honours, a teaching-assistant role -- copied '
        'verbatim as one string, and "" if the entry has none. Do not summarise '
        'it: these are the named subjects a reviewer matches against a job '
        'description.'
    ),
    "skills": (
        'Return {"skills": ["Category: item, item", "Category: item"]} — a '
        'LIST OF STRINGS, one per category, each "Category: comma, separated, '
        'items". Never a single string, never an object.'
    ),
    "certifications": (
        'Return {"certifications": ["", ""]} — one string per certification. '
        'Ignore any other line in this block: spoken languages, availability and '
        'similar do not belong on this resume (user instruction 2026-09-30). '
        'Work authorisation is already a first-class profile field '
        '(users.visa_status, read by shared/work_auth.py), so it is not needed '
        'here either.'
    ),
}

# Sections carried through verbatim. Asking a model to restate prose it is
# meant to preserve only invites it to improve the wording.
_VERBATIM = ("summary", "awards", "publications")


def _strip_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = "\n".join(ln for ln in raw.split("\n") if not ln.strip().startswith("```")).strip()
    return raw


def _parse_one(ai_client, section: str, body: str) -> dict:
    """Parse one section. Returns {} on any failure — never raises.

    Per-section isolation is the point: a model that chokes on Experience must
    not cost us Education as well, which is exactly what one whole-document
    call did.
    """
    if len(body) > MAX_SECTION_CHARS:
        logger.warning(
            "[parse] section %r is %d chars; sending the first %d. "
            "The remainder is NOT parsed.",
            section, len(body), MAX_SECTION_CHARS,
        )
        body = body[:MAX_SECTION_CHARS]

    prompt = f"{_SECTION_PROMPTS[section]}\n\nText:\n{body}\n\nReturn ONLY valid JSON."
    try:
        raw = ai_client.complete(prompt=prompt, system=_SYSTEM, temperature=0.1)
        parsed = json.loads(_strip_fences(raw))
    except Exception as exc:
        logger.error("[parse] section %r failed: %s", section, exc)
        return {}
    if not isinstance(parsed, dict):
        # Valid JSON of the wrong type. The old code returned this straight to
        # the caller, so parse_resume_sections could hand back a list or a str
        # against its own annotation and crash app.py with AttributeError.
        logger.error("[parse] section %r returned %s, not an object",
                     section, type(parsed).__name__)
        return {}
    return parsed


def parse_resume_sections(text: str, ai_client=None) -> Dict:
    """Parse résumé text into structured sections.

    Returns a dict — always, whatever happens — carrying ``raw_text`` plus
    whichever of name/email/phone/location/title_line/summary/skills/
    experience/projects/education/certifications could be extracted.

    Without an ``ai_client`` it still returns the deterministic segmentation,
    which is strictly more than the old ``{"raw_text": ...}``: summary, skills
    and certifications come through usable, and the caller can see which
    sections exist.
    """
    if not text:
        return {"raw_text": ""}

    segments = segment_sections(text)
    out: Dict = {"raw_text": text, "_sections_found": sorted(segments)}

    # Verbatim sections need no model.
    for name in _VERBATIM:
        if segments.get(name):
            out[name] = segments[name]

    if not ai_client:
        # Best-effort structure with no model available. Skills as a list of
        # lines matches what the renderer's adapter wants; certifications the
        # same. Experience and projects genuinely need parsing, so they are
        # left absent rather than guessed at.
        if segments.get("skills"):
            out["skills"] = [ln.strip() for ln in segments["skills"].splitlines() if ln.strip()]
        if segments.get("certifications"):
            out["certifications"] = [
                ln.strip().lstrip("•-*– ").strip()
                for ln in segments["certifications"].splitlines() if ln.strip()
            ]
        logger.info("[parse] no ai_client; returning deterministic segmentation only")
        return out

    for name, body in segments.items():
        if name not in _SECTION_PROMPTS or not body:
            continue
        out.update(_parse_one(ai_client, name, body))

    missing = [s for s in ("experience", "projects", "education")
               if s in segments and not out.get(s)]
    if missing:
        logger.warning("[parse] present in the document but not parsed: %s", missing)

    return out


# Kept for callers that still import it under the old name.
__all__ = ["extract_text_from_pdf", "parse_resume_sections", "segment_sections",
           "MAX_SECTION_CHARS"]
