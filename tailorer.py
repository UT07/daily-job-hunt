"""Resume tailoring engine using multi-provider AI client.

Takes the base LaTeX resume and tweaks it for each specific job,
emphasizing relevant skills, adjusting the summary, and reordering bullet points.
"""

from __future__ import annotations
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING
from scrapers.base import Job
from ai_client import AIClient
from latex_compiler import _sanitize_latex
from quality_logger import log_quality
from utils.keyword_extractor import extract_keywords

if TYPE_CHECKING:
    from user_profile import UserProfile

logger = logging.getLogger(__name__)

CRITIC_RUBRIC_PROMPT = """You are evaluating two resume tailoring attempts. Pick the BETTER one.

EVALUATION CRITERIA (score each 1-10):
1. KEYWORD COVERAGE: Does the resume address the top JD keywords? Count how many of the required keywords appear.
2. SECTION COMPLETENESS: Are all 6 sections present and substantive (Summary, Skills, Experience, Projects, Education, Certifications)?
3. WRITING QUALITY: Are bullet points specific with metrics? Are action verbs strong? Is language authentic (no AI filler)?
4. NO FABRICATION: Does the resume only claim skills/experience present in the original? Flag any suspicious additions.

Return JSON: {"winner": "A" or "B", "scores_a": {"keywords": N, "sections": N, "quality": N, "fabrication": N}, "scores_b": {...}, "reason": "..."}"""

LENGTH_GUIDANCE = """
TARGET LENGTH: 850-1000 words of content for exactly 2 pages.

SECTION WORD BUDGETS:
- Summary: 40-60 words
- Skills: 50-80 words
- Each Experience entry: 80-120 words (3-4 bullet points)
- Each Project: 60-90 words
- Education: 30-50 words
- Certifications: 20-30 words
"""


def get_tailoring_depth(base_score: float | None) -> tuple[str, int]:
    """Determine tailoring depth and max improvement rounds from base score."""
    if base_score is None or base_score < 0:
        return "moderate", 2

    if base_score >= 85:
        return (
            "LIGHT TOUCH: Make surgical keyword additions and minor description tweaks only. "
            "Do not restructure sections or rewrite bullets.",
            1,
        )
    elif base_score >= 70:
        return (
            "MODERATE REWRITE: Restructure bullet points to match JD priorities. "
            "Rewrite summary section. Reorder skills to lead with JD-relevant ones.",
            2,
        )
    else:
        return (
            "HEAVY REWRITE: Full project description rewrites emphasizing JD relevance. "
            "Overhaul summary. Reprioritize entire skills section. "
            "Restructure experience bullets with metrics and impact.",
            3,
        )


def should_tailor(job: dict) -> bool:
    """Check if a job should be tailored. Returns False if data is insufficient."""
    if job.get("score_status") == "insufficient_data":
        return False
    if job.get("score_status") == "incomplete":
        return False
    return True


TAILOR_SYSTEM_PROMPT = r"""You are an expert resume writer who tailors technical resumes for specific job listings. You work with LaTeX resume BODIES (the content between \begin{document} and \end{document} only).

CRITICAL OUTPUT RULE:
You will receive ONLY the document body. Return ONLY the tailored body.
- Do NOT emit \documentclass, \usepackage, \newcommand, \setlength, \titleformat, \geometry, or any preamble command.
- Do NOT emit \begin{document} or \end{document}.
- The base preamble (including the custom macros below) is managed outside of AI. Just return the body content.

REQUIRED HEADER BLOCK (MUST appear at the very top of the body, with all contact details preserved — you MAY only change the \normalsize title line to emphasize tech relevant to the JD):
  \begin{center}
  {\Large \textbf{Utkarsh Singh}}\\[0.04em]
  {\normalsize <role title with tech tags>}\\[0.08em]
  Dublin, Ireland \textbar\ +353 892515620 \textbar\ \href{mailto:254utkarsh@gmail.com}{254utkarsh@gmail.com}\\[0.08em]
  \href{https://github.com/UT07}{github.com/UT07} \textbar\ \href{https://www.linkedin.com/in/utkarshsingh2001/}{linkedin.com/in/utkarshsingh2001} \textbar\ \href{https://utworld.netlify.app}{utworld.netlify.app}
  \end{center}

REQUIRED SECTION HEADERS (your body output MUST contain ALL six, each on its own line, each followed by its content — do NOT drop any):
  \section*{Summary}
  \section*{Technical Skills}
  \section*{Experience}
  \section*{Featured Projects}
  \section*{Education}
  \section*{Certifications}

CUSTOM MACROS (already defined — use with EXACT argument counts):
- \jobentry{company}{location}{dates}{title}       — 4 args
  Example: \jobentry{Clover IT Services}{New York, NY (Remote)}{Jun 2022 -- Jul 2024}{\textbf{\textit{Software Engineer}}}
- \projectentry{name}{dates}{tech}                 — 3 args (no URL)
  Example: \projectentry{WhatsTheCraic}{Apr 2025 -- Jul 2025}{Node.js, React, FastAPI, MySQL, Docker, AWS}
- \projectentryurl{name}{dates}{url}{url-text}{tech} — 5 args (with clickable URL)
  Example: \projectentryurl{Purrrfect Keys}{Jan 2026 -- Present}{https://expo.dev/accounts/ut254/projects/purrrfect-keys}{expo.dev/accounts/ut254/projects/purrrfect-keys}{React Native, TypeScript, Firebase}

Each macro call MUST be followed by a \begin{itemize}...\end{itemize} block with \item bullets. Do NOT put \begin{itemize} inside the macro call.

RULES:
1. NEVER fabricate experience, skills, or accomplishments. Only reword, reorder, and emphasize what already exists.
2. Make targeted, surgical edits. Do NOT rewrite the entire resume.
3. Focus changes on:
   - Summary: adjust emphasis for this role
   - Skills: reorder to put the most relevant first; add more relevant skills from the base if they match the JD
   - Experience bullets: reorder within each job; tweak wording to match the job listing's terminology
   - Projects: ALWAYS KEEP "Purrrfect Keys" (it is the candidate's largest project and shows end-to-end ownership). Then SELECT 2 more from the remaining 4 projects (WhatsTheCraic, Genomic Benchmarking, NaukriBaba, UTWorld) based on relevance to the KEY JD REQUIREMENTS below. REMOVE the other 2 entirely. Rewrite ALL project descriptions to emphasize aspects matching the JD — the same project should highlight different strengths for different jobs.
4. The resume must remain truthful.
5. PAGE LAYOUT (CRITICAL):
   - The resume MUST be exactly TWO PAGES. No more, no less.
   - Page 1: Header, Summary, Technical Skills, Clover IT Services (7 bullets), and Seattle Kraken (3 bullets).
   - Page 2: 3 selected Projects (Purrrfect Keys always + 2 others), Education, Certifications.
   - If content overflows to page 3, CUT bullet points (trim Clover to 6, Kraken to 2).
   - Do NOT add extra bullets to any section. Keep Clover at 7, Kraken at 3.
6. Prominently place technologies the candidate has used that the job mentions.

WRITING STYLE (CRITICAL):
- Do NOT use em-dashes (---, --, or the — character) as clause connectors. Use periods to end sentences.
- Do NOT use filler phrases: "directly transferable to", "aligned with", "outcomes relevant to", "leveraging", "utilizing", "showcasing", "demonstrating proficiency in".
- Write short, direct sentences in active voice. Lead with the action verb.
- Do NOT append company-specific qualifiers to bullet points (e.g., "practices aligned with Company's GitOps patterns"). The bullet should stand on its own.
- Quantify impact with numbers and percentages where they already exist.
- Match job posting keywords by naturally weaving them into existing bullets, not by adding new sentences about them.

Return ONLY the tailored body content. No explanations, no markdown fences, no preamble commands."""


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from AI output, handling ```latex, ```tex, etc."""
    text = text.strip()
    if "```" in text:
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Skip lines that are just code fences (possibly with language tag)
            if re.match(r'^```\s*\w*\s*$', stripped):
                continue
            cleaned.append(line)
        text = "\n".join(cleaned).strip()
    return text


def _split_tex(tex: str) -> tuple[str, str]:
    """Split a LaTeX source into (preamble, body).

    The preamble is everything before ``\\begin{document}`` (exclusive).
    The body is everything between ``\\begin{document}`` and the LAST
    ``\\end{document}`` (exclusive of both markers).

    Using ``rfind`` for the end marker guards against AI output that
    terminates early with ``\\end{document}`` and then rambles.

    Returns ("", tex) if no ``\\begin{document}`` marker is present.
    """
    begin_marker = "\\begin{document}"
    end_marker = "\\end{document}"
    begin_idx = tex.find(begin_marker)
    if begin_idx < 0:
        return "", tex
    preamble = tex[:begin_idx].rstrip()
    end_idx = tex.rfind(end_marker)
    if end_idx < 0 or end_idx <= begin_idx:
        body = tex[begin_idx + len(begin_marker):].strip()
    else:
        body = tex[begin_idx + len(begin_marker):end_idx].strip()
    return preamble, body


def _splice_tex(preamble: str, body: str) -> str:
    """Glue a preamble and body back into a complete LaTeX document."""
    return f"{preamble}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


def _count_macro_args(tex: str, start: int) -> int:
    """Count the number of balanced ``{...}`` groups starting at ``start``.

    Skips whitespace between groups. Respects backslash-escaped braces.
    Used to validate that macro calls have the correct number of arguments.
    """
    i = start
    count = 0
    while i < len(tex):
        # Skip whitespace between groups
        while i < len(tex) and tex[i].isspace():
            i += 1
        if i >= len(tex) or tex[i] != "{":
            break
        # Walk balanced braces starting at i
        depth = 0
        j = i
        closed = False
        while j < len(tex):
            ch = tex[j]
            if ch == "\\" and j + 1 < len(tex):
                j += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    count += 1
                    i = j + 1
                    closed = True
                    break
            j += 1
        if not closed:
            break
    return count


# Arity of the custom macros defined in the base resume preamble.
# Checked in the order listed so the longer name (\projectentryurl) is
# matched before its prefix (\projectentry).
_MACRO_ARITIES: dict[str, int] = {
    "projectentryurl": 5,
    "projectentry": 3,
    "jobentry": 4,
}


def _validate_macro_arities(tex: str) -> list[str]:
    """Return a list of arity-mismatch descriptions for known custom macros.

    Empty list means every call site has the expected number of ``{}`` args.
    Uses a word-boundary negative lookahead so ``\\projectentryurl`` is not
    matched as ``\\projectentry`` (and double-counted).
    """
    issues: list[str] = []
    for macro, expected in _MACRO_ARITIES.items():
        pattern = re.compile(r"\\" + macro + r"(?![a-zA-Z])")
        for match in pattern.finditer(tex):
            # Skip definitions like \newcommand{\jobentry}[4]{...} where the
            # macro name sits inside the \newcommand{...} wrapper.
            backslash_pos = match.start()
            if backslash_pos > 0 and tex[backslash_pos - 1] == "{":
                continue
            actual = _count_macro_args(tex, match.end())
            if actual != expected:
                issues.append(
                    f"\\{macro} @ pos {match.start()} has {actual} args (expected {expected})"
                )
    return issues


def _validate_latex_structure(tailored_tex: str, base_tex: str, company: str) -> str:
    """Validate that the AI-generated LaTeX preserves critical structural elements.

    Checks for:
    - Required sections (Experience, Skills, Education)
    - Custom macro definitions (\\jobentry, \\projectentry, etc.)
    - \\begin{document} and \\end{document}
    - Balanced braces (approximate check)

    Falls back to base_tex if critical structure is missing.
    """
    # Substring match: accept "Work Experience", "Technical Skills", "Professional
    # Experience", etc. Matches the compiler's check_section_completeness logic so
    # we don't reject outputs the compiler would happily accept.
    required_sections = ["experience", "skills", "education"]
    section_heads = [
        h.lower() for h in re.findall(r"\\section\*?\{([^}]*)\}", tailored_tex)
    ]
    missing = [s for s in required_sections if not any(s in h for h in section_heads)]
    if missing:
        logger.warning(
            f"[TAILOR] {company}: tailored resume missing sections: {missing} "
            f"(saw: {section_heads}). Using base."
        )
        return base_tex

    # Check that custom macros weren't destroyed
    if "\\newcommand{\\jobentry}" in base_tex and "\\jobentry" not in tailored_tex:
        logger.warning(f"[TAILOR] {company}: \\jobentry macro lost in tailoring. Using base.")
        return base_tex

    if "\\begin{document}" not in tailored_tex:
        logger.warning(f"[TAILOR] {company}: \\begin{{document}} missing. Using base.")
        return base_tex

    # Approximate brace balance check
    open_braces = tailored_tex.count("{") - tailored_tex.count("\\{")
    close_braces = tailored_tex.count("}") - tailored_tex.count("\\}")
    if abs(open_braces - close_braces) > 5:
        logger.warning(
            f"[TAILOR] {company}: severe brace imbalance "
            f"(open={open_braces}, close={close_braces}). Using base."
        )
        return base_tex

    # Custom macro arity check: catch AI mistakes like \projectentryurl{a}{b}{c}{d}
    # (4 args) when the macro is defined as taking 5.
    arity_issues = _validate_macro_arities(tailored_tex)
    if arity_issues:
        logger.warning(
            f"[TAILOR] {company}: macro arity mismatch: "
            f"{'; '.join(arity_issues[:3])}. Using base."
        )
        return base_tex

    # Header-block check: AI sometimes drops the \begin{center}...contact
    # details...\end{center} block because our "no preamble" instruction spills
    # into overcorrection. The name + email MUST survive — recruiters can't
    # reach the candidate otherwise.
    _HEADER_MARKERS = ["Utkarsh Singh", "254utkarsh@gmail.com"]
    missing_header = [m for m in _HEADER_MARKERS if m not in tailored_tex]
    if missing_header:
        logger.warning(
            f"[TAILOR] {company}: header block dropped (missing: {missing_header}). "
            f"Using base."
        )
        return base_tex

    return tailored_tex


def tailor_resume(
    job: Job,
    base_tex: str,
    ai_client: AIClient,
    output_dir: Path,
    user_profile: Optional["UserProfile"] = None,
) -> str:
    """Tailor a LaTeX resume for a specific job listing.

    Parameters
    ----------
    user_profile:
        Optional UserProfile. When provided, the generated filename uses
        ``user_profile.safe_filename_prefix()`` instead of the hardcoded
        ``"Utkarsh_Singh"`` prefix.

    Returns the path to the tailored .tex file.
    """
    # Compute base score from initial match scores (average of ATS/HM/TR)
    base_score = None
    if job.match_score and job.match_score > 0:
        base_score = job.match_score
    elif job.ats_score and job.ats_score > 0:
        scores_list = [s for s in [job.ats_score, job.hiring_manager_score, job.tech_recruiter_score] if s and s > 0]
        base_score = sum(scores_list) / len(scores_list) if scores_list else None

    depth_instruction, _max_rounds = get_tailoring_depth(base_score)
    logger.info(f"[TAILOR] {job.company}: base_score={base_score}, depth={depth_instruction[:30]}...")

    # Extract top keywords from JD for targeted tailoring
    keywords = extract_keywords(job.description, max_keywords=10)
    keyword_section = ""
    if keywords:
        keyword_section = f"\n\nKEY JD REQUIREMENTS: {', '.join(keywords)}\nEnsure each of these is addressed in the resume."

    # Build dynamic system prompt with depth, length guidance, and keywords
    system_prompt = f"TAILORING DEPTH: {depth_instruction}\n{LENGTH_GUIDANCE}\n{TAILOR_SYSTEM_PROMPT}{keyword_section}"

    # Get tailoring suggestions from the matching step if available
    suggestions = ""
    if hasattr(job, "_match_data") and job._match_data:
        sugg_list = job._match_data.get("tailoring_suggestions", [])
        key_matches = job._match_data.get("key_matches", [])
        gaps = job._match_data.get("gaps", [])
        suggestions = f"""
TAILORING CONTEXT FROM MATCHING ANALYSIS:
- Key skill matches: {', '.join(key_matches)}
- Gaps to address (de-emphasize or contextualize): {', '.join(gaps)}
- Specific suggestions: {'; '.join(sugg_list)}"""

    # Split base resume: AI only sees the body, never the preamble.
    # Preamble (packages, \newcommand macros) is spliced back in after AI response.
    base_preamble, base_body = _split_tex(base_tex)
    if not base_preamble:
        logger.error(f"[TAILOR] {job.company}: base resume missing \\begin{{document}}. Using base.")
        return ""

    user_prompt = f"""Tailor this resume body for the following job:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Type: {job.job_type or 'Full-time'}

JOB DESCRIPTION:
{job.description[:4000]}
{suggestions}

BASE RESUME BODY (content between \\begin{{document}} and \\end{{document}} only):
{base_body}

Return ONLY the tailored body. Do not include \\documentclass, \\usepackage, \\newcommand, or \\begin{{document}}/\\end{{document}} — they are managed separately.

Reminder: your output MUST contain all six section headers verbatim: \\section*{{Summary}}, \\section*{{Technical Skills}}, \\section*{{Experience}}, \\section*{{Featured Projects}}, \\section*{{Education}}, \\section*{{Certifications}}."""

    try:
        # Include a hash of the base resume in the cache key so that a changed
        # base resume always produces a fresh tailoring result, even if the rest
        # of the prompt text happens to be identical.
        resume_hash = hashlib.md5(base_tex.encode()).hexdigest()

        # Use council if available (3 models generate, 2 critique, best wins)
        use_council = hasattr(ai_client, 'council_complete') and len(getattr(ai_client, 'providers', [])) >= 3
        if use_council:
            logger.info(f"[TAILOR] Using council for {job.company}")
            tailored_tex = ai_client.council_complete(
                prompt=user_prompt,
                system=system_prompt,
                n_generators=2,
                n_critics=1,
                task_description=CRITIC_RUBRIC_PROMPT,
                temperature=0.3,
                cache_extra=resume_hash,
            )
            job.tailoring_provider = getattr(ai_client, "last_council_provider", "council")
            job.tailoring_model = getattr(ai_client, "last_council_model", "consensus")
        else:
            info = ai_client.complete_with_info(
                prompt=user_prompt,
                system=system_prompt,
                temperature=0.3,
                cache_extra=resume_hash,
            )
            tailored_tex = info["response"].strip()
            job.tailoring_provider = info["provider"]
            job.tailoring_model = info["model"]

        # Strip markdown code fences if present (handles ```latex, ```tex, etc.)
        tailored_tex = _strip_code_fences(tailored_tex)

        # Extract the body: if AI returned a full document despite instructions,
        # strip its preamble. Otherwise use the response as-is as body content.
        if "\\begin{document}" in tailored_tex:
            _ignored_preamble, ai_body = _split_tex(tailored_tex)
        else:
            # AI returned just a body (the expected case)
            ai_body = tailored_tex.strip()
            # Defensive: strip any trailing \end{document} the AI may have added
            ai_body = ai_body.removesuffix("\\end{document}").strip()

        if not ai_body:
            logger.warning(f"[TAILOR] {job.company}: AI returned empty body. Using base.")
            return ""

        # Splice: base preamble (known good) + AI body + \end{document}
        tailored_tex = _splice_tex(base_preamble, ai_body)

        # Validate structural integrity: check that key LaTeX structures survived
        tailored_tex = _validate_latex_structure(tailored_tex, base_tex, job.company)

        # Fix common AI LaTeX typos before sanitization
        _TYPO_FIXES = {
            "\\emphergencystretch": "\\emergencystretch",
            "\\emergecystretch": "\\emergencystretch",
            "\\emergenystretch": "\\emergencystretch",
            "\\setlength{\\emergencystrech}": "\\setlength{\\emergencystretch}",
            "\\usepackge": "\\usepackage",
            "\\documenclass": "\\documentclass",
            "\\begindocument": "\\begin{document}",
        }
        for typo, fix in _TYPO_FIXES.items():
            if typo in tailored_tex:
                tailored_tex = tailored_tex.replace(typo, fix)
                logger.info(f"[TYPO FIX] Fixed '{typo}' -> '{fix}'")

        # Sanitize LaTeX before saving (escape bare &, #, % from AI output)
        tailored_tex = _sanitize_latex(tailored_tex)

        # Save tailored .tex file
        safe_title = "".join(c for c in job.title if c.isalnum() or c in " _-")[:30].strip()
        safe_company = "".join(c for c in job.company if c.isalnum() or c in " _-")[:30].strip()
        date_str = datetime.now().strftime("%Y-%m-%d")
        name_prefix = user_profile.safe_filename_prefix() if user_profile else "Utkarsh_Singh"
        filename = f"{name_prefix}_{safe_title}_{safe_company}_{date_str}".replace(" ", "_")
        tex_path = output_dir / f"{filename}.tex"
        tex_path.write_text(tailored_tex, encoding="utf-8")

        job.tailored_tex_path = str(tex_path)
        logger.info(f"[TAILORED] {job.title} @ {job.company} -> {tex_path.name} by {job.tailoring_provider}:{job.tailoring_model}")
        log_quality(task="tailor_resume", provider=job.tailoring_provider, model=job.tailoring_model, job_id=job.job_id, company=job.company, job_title=job.title)
        return str(tex_path)

    except Exception as e:
        logger.error(f"Error tailoring for {job.company}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Plain-text tailoring for Google Docs templates
# ---------------------------------------------------------------------------

TAILOR_TEXT_SYSTEM_PROMPT = """You are an expert resume writer who tailors technical resumes for specific job listings. You work with plain text resume sections (NOT LaTeX) intended for Google Docs placeholder replacement.

RULES:
1. NEVER fabricate experience, skills, or accomplishments. Only reword, reorder, and emphasize what already exists.
2. Keep the exact same plain-text structure for each section — do NOT introduce LaTeX commands, markdown, or HTML.
3. Make targeted, surgical edits. Do NOT rewrite entire sections from scratch.
4. Focus changes on:
   - SUMMARY: adjust emphasis and keyword alignment for this specific role (3–4 sentences)
   - SKILLS: reorder skill lines so the most relevant categories appear first; adjust the item order within each line
   - Experience bullet points (CLOVER_BULLETS, KRAKEN_BULLETS): reorder and lightly reword to match job terminology
   - Project bullet points (PROJECT_1_BULLETS, PROJECT_2_BULLETS, PROJECT_3_BULLETS): emphasize the most relevant aspects
   - TITLE_LINE: update the parenthetical tech list to lead with the most relevant skills for this role
5. The resume must remain truthful.

WRITING STYLE (CRITICAL):
- Do NOT use em-dashes (---, --, or the — character) as clause connectors. Use periods to end sentences.
- Do NOT use filler phrases: "directly transferable to", "aligned with", "outcomes relevant to", "leveraging", "utilizing", "showcasing", "demonstrating proficiency in".
- Write short, direct sentences in active voice. Lead with the action verb.
- Do NOT append company-specific qualifiers to bullet points (e.g., "practices aligned with Company's GitOps patterns"). The bullet should stand on its own.
- Quantify impact with numbers and percentages where they already exist in the base content.
- Match job posting keywords by naturally weaving them into existing bullets, not by adding new sentences about them.

OUTPUT FORMAT (CRITICAL):
- Return ONLY valid JSON. No explanations, no markdown fences, no extra keys.
- The JSON object must contain exactly the same keys that were provided in the base sections.
- Each bullet list value (CLOVER_BULLETS, KRAKEN_BULLETS, PROJECT_N_BULLETS) must be a string where each bullet is on its own line starting with "• ".
- SKILLS must be a string where each skill category is on its own line in the format "Category Name: item1, item2, ...".
- SUMMARY must be a plain text string of 3–4 sentences.
- TITLE_LINE must be a plain text string like "Site Reliability Engineer (Python, Kubernetes, AWS, Observability)".

Example JSON shape (keys will vary based on input):
{
  "TITLE_LINE": "...",
  "SUMMARY": "...",
  "SKILLS": "...",
  "CLOVER_BULLETS": "• bullet one\\n• bullet two",
  "KRAKEN_BULLETS": "• bullet one\\n• bullet two",
  "PROJECT_1_BULLETS": "• bullet one\\n• bullet two",
  "PROJECT_2_BULLETS": "• bullet one\\n• bullet two",
  "PROJECT_3_BULLETS": "• bullet one\\n• bullet two"
}"""


def tailor_resume_text(
    job: Job,
    base_sections: Dict[str, str],
    ai_client: AIClient,
    user_profile: Optional["UserProfile"] = None,
) -> Dict[str, str]:
    """Tailor resume sections as plain text for Google Docs placeholder replacement.

    Parameters
    ----------
    job:
        Job object with title, company, description, location, job_type.
    base_sections:
        Dict with keys like "SUMMARY", "SKILLS", "CLOVER_BULLETS",
        "KRAKEN_BULLETS", "PROJECT_1_BULLETS", etc. — the default content for
        each section.
    ai_client:
        Configured AIClient instance.
    user_profile:
        Optional UserProfile. Currently reserved for future prompt injection;
        accepted here for API consistency with ``tailor_resume()``.

    Returns
    -------
    Dict with the same keys as ``base_sections`` but with content tailored for
    the specific job.  Falls back to ``base_sections`` values for any key the
    AI fails to return.
    """
    suggestions = ""
    if hasattr(job, "_match_data") and job._match_data:
        sugg_list = job._match_data.get("tailoring_suggestions", [])
        key_matches = job._match_data.get("key_matches", [])
        gaps = job._match_data.get("gaps", [])
        suggestions = f"""
TAILORING CONTEXT FROM MATCHING ANALYSIS:
- Key skill matches: {', '.join(key_matches)}
- Gaps to address (de-emphasize or contextualize): {', '.join(gaps)}
- Specific suggestions: {'; '.join(sugg_list)}"""

    sections_json = json.dumps(base_sections, indent=2, ensure_ascii=False)

    user_prompt = f"""Tailor the following resume sections for this job listing and return ONLY a JSON object with the same keys.

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Type: {job.job_type or 'Full-time'}

JOB DESCRIPTION:
{job.description[:4000]}
{suggestions}

BASE RESUME SECTIONS (plain text, same keys required in your JSON response):
{sections_json}

Return ONLY valid JSON with the same keys. No markdown, no explanation."""

    try:
        sections_hash = hashlib.md5(sections_json.encode()).hexdigest()

        # Use council if available
        use_council = hasattr(ai_client, 'council_complete') and len(getattr(ai_client, 'providers', [])) >= 3
        if use_council:
            logger.info(f"[TAILOR TEXT] Using council for {job.company}")
            raw_response = ai_client.council_complete(
                prompt=user_prompt,
                system=TAILOR_TEXT_SYSTEM_PROMPT,
                n_generators=2,
                n_critics=1,
                task_description=CRITIC_RUBRIC_PROMPT,
                temperature=0.3,
                cache_extra=sections_hash,
            )
            job.tailoring_provider = getattr(ai_client, "last_council_provider", "council")
            job.tailoring_model = getattr(ai_client, "last_council_model", "consensus")
        else:
            info = ai_client.complete_with_info(
                prompt=user_prompt,
                system=TAILOR_TEXT_SYSTEM_PROMPT,
                temperature=0.3,
                cache_extra=sections_hash,
            )
            raw_response = info["response"].strip()
            job.tailoring_provider = info["provider"]
            job.tailoring_model = info["model"]

        # Strip markdown code fences if present
        if raw_response.startswith("```"):
            lines = raw_response.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_response = "\n".join(lines).strip()

        tailored: Dict[str, str] = json.loads(raw_response)

        # Validate: ensure all expected keys are present; fall back where missing
        result: Dict[str, str] = {}
        for key, base_value in base_sections.items():
            if key in tailored and isinstance(tailored[key], str) and tailored[key].strip():
                result[key] = tailored[key]
            else:
                logger.warning(
                    f"[TAILOR TEXT] Missing or empty key '{key}' for {job.company}, using base content"
                )
                result[key] = base_value

        logger.info(f"[TAILOR TEXT] {job.title} @ {job.company} -> {len(result)} sections tailored by {job.tailoring_provider}:{job.tailoring_model}")
        log_quality(task="tailor_text", provider=job.tailoring_provider, model=job.tailoring_model, job_id=job.job_id, company=job.company, job_title=job.title)
        return result

    except json.JSONDecodeError as e:
        logger.error(f"[TAILOR TEXT] JSON parse error for {job.company}: {e}. Using base sections.")
        return dict(base_sections)
    except Exception as e:
        logger.error(f"[TAILOR TEXT] Error tailoring text for {job.company}: {e}")
        return dict(base_sections)


def extract_base_sections(tex_content: str, resume_type: str = "sre_devops") -> Dict[str, str]:
    """Parse a LaTeX resume file and extract plain text content for each section.

    This bridges existing LaTeX resume files into the plain-text Google Docs flow.
    The returned dict uses the same placeholder keys expected by ``tailor_resume_text``.

    Parameters
    ----------
    tex_content:
        Raw LaTeX source of the resume.
    resume_type:
        Identifier for the resume variant (currently only "sre_devops" is supported).

    Returns
    -------
    Dict with keys: TITLE_LINE, SUMMARY, SKILLS, CLOVER_BULLETS, KRAKEN_BULLETS,
    PROJECT_1_BULLETS, PROJECT_2_BULLETS, PROJECT_3_BULLETS.
    """

    def _strip_latex(text: str) -> str:
        """Remove common LaTeX commands and return readable plain text."""
        # Unwrap \textbf{...}, \textit{...}, \emph{...}, \texttt{...}
        text = re.sub(r"\\text(?:bf|it|tt|rm|sc|sf)\{([^}]*)\}", r"\1", text)
        text = re.sub(r"\\emph\{([^}]*)\}", r"\1", text)
        # Remove \href{url}{text} -> text
        text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
        # Remove \textbar\ and \textbar
        text = re.sub(r"\\textbar\\?", "|", text)
        # Remove \hfill
        text = re.sub(r"\\hfill\s*", " ", text)
        # Remove \, (thin space)
        text = re.sub(r"\\,", "", text)
        # Strip remaining simple commands like \textless \textgreater
        text = re.sub(r"\\textless", "<", text)
        text = re.sub(r"\\textgreater", ">", text)
        # Remove leftover LaTeX commands \foo or \foo{...}
        text = re.sub(r"\\[a-zA-Z]+\*?\{[^}]*\}", "", text)
        text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
        # Clean up braces
        text = re.sub(r"[{}]", "", text)
        # Normalise whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = text.strip()
        return text

    def _extract_itemize_bullets(block: str) -> str:
        """Extract \\item lines from a LaTeX itemize block and return as bullet lines."""
        items = re.findall(r"\\item\s+(.*?)(?=\\item|\\end\{itemize\}|$)", block, re.DOTALL)
        bullets = []
        for item in items:
            cleaned = _strip_latex(item.replace("\n", " "))
            # Collapse multiple spaces
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                bullets.append(f"• {cleaned}")
        return "\n".join(bullets)

    sections: Dict[str, str] = {}

    # --- TITLE_LINE ---
    title_match = re.search(
        r"\\normalsize\s+(.*?)\\\\",
        tex_content,
        re.DOTALL,
    )
    if title_match:
        sections["TITLE_LINE"] = _strip_latex(title_match.group(1))
    else:
        sections["TITLE_LINE"] = "Site Reliability Engineer (Python, Kubernetes, AWS, Observability)"

    # --- SUMMARY ---
    summary_match = re.search(
        r"section\*\{Summary\}(.*?)(?=\\section|\Z)",
        tex_content,
        re.DOTALL,
    )
    if summary_match:
        sections["SUMMARY"] = _strip_latex(summary_match.group(1).replace("\n", " "))
    else:
        sections["SUMMARY"] = ""

    # --- SKILLS ---
    skills_match = re.search(
        r"section\*\{Technical Skills\}.*?\\begin\{itemize\}(.*?)\\end\{itemize\}",
        tex_content,
        re.DOTALL,
    )
    if skills_match:
        skill_items = re.findall(
            r"\\item\s+(.*?)(?=\\item|\\end\{itemize\}|$)",
            skills_match.group(1),
            re.DOTALL,
        )
        skill_lines = []
        for item in skill_items:
            cleaned = _strip_latex(item.replace("\n", " "))
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                skill_lines.append(cleaned)
        sections["SKILLS"] = "\n".join(skill_lines)
    else:
        sections["SKILLS"] = ""

    # --- CLOVER BULLETS ---
    clover_match = re.search(
        r"jobentry\{Clover IT Services\}.*?\\begin\{itemize\}(.*?)\\end\{itemize\}",
        tex_content,
        re.DOTALL,
    )
    if clover_match:
        sections["CLOVER_BULLETS"] = _extract_itemize_bullets(clover_match.group(1))
    else:
        sections["CLOVER_BULLETS"] = ""

    # --- KRAKEN BULLETS ---
    kraken_match = re.search(
        r"jobentry\{Seattle Kraken[^}]*\}.*?\\begin\{itemize\}(.*?)\\end\{itemize\}",
        tex_content,
        re.DOTALL,
    )
    if kraken_match:
        sections["KRAKEN_BULLETS"] = _extract_itemize_bullets(kraken_match.group(1))
    else:
        sections["KRAKEN_BULLETS"] = ""

    # --- PROJECT BULLETS ---
    # Find all \projectentry* blocks followed by itemize environments
    project_blocks = re.findall(
        r"\\projectentry(?:url)?\{[^}]+\}.*?\\begin\{itemize\}(.*?)\\end\{itemize\}",
        tex_content,
        re.DOTALL,
    )
    for idx, block in enumerate(project_blocks[:3], start=1):
        sections[f"PROJECT_{idx}_BULLETS"] = _extract_itemize_bullets(block)
    # Ensure keys always exist even if fewer than 3 projects are found
    for idx in range(1, 4):
        sections.setdefault(f"PROJECT_{idx}_BULLETS", "")

    return sections
