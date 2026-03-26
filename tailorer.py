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

if TYPE_CHECKING:
    from user_profile import UserProfile

logger = logging.getLogger(__name__)


TAILOR_SYSTEM_PROMPT = r"""You are an expert resume writer who tailors technical resumes for specific job listings. You work with LaTeX resumes.

RULES:
1. NEVER fabricate experience, skills, or accomplishments. Only reword, reorder, and emphasize what already exists.
2. Keep the exact same LaTeX structure, commands, and formatting.
3. Make targeted, surgical edits. Do NOT rewrite the entire resume.
4. Focus changes on:
   - Summary: adjust emphasis for this role
   - Skills: reorder to put the most relevant first
   - Experience bullets: reorder within each job; tweak wording to match the job listing's terminology
   - Projects: emphasize the most relevant one
5. The resume must remain truthful.
6. The resume MUST be exactly TWO PAGES. Page 1: Header, Summary, Skills, and the Clover IT Services experience. Page 2: Seattle Kraken, Projects, Education, Certifications. Do NOT cram everything onto one page.
7. Prominently place technologies the candidate has used that the job mentions.

WRITING STYLE (CRITICAL):
- Do NOT use em-dashes (---, --, or the — character) as clause connectors. Use periods to end sentences.
- Do NOT use filler phrases: "directly transferable to", "aligned with", "outcomes relevant to", "leveraging", "utilizing", "showcasing", "demonstrating proficiency in".
- Write short, direct sentences in active voice. Lead with the action verb.
- Do NOT append company-specific qualifiers to bullet points (e.g., "practices aligned with Company's GitOps patterns"). The bullet should stand on its own.
- Quantify impact with numbers and percentages where they already exist.
- Match job posting keywords by naturally weaving them into existing bullets, not by adding new sentences about them.

Return ONLY the complete, modified LaTeX source code. No explanations, no markdown fences, just pure LaTeX starting with \documentclass."""


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

    user_prompt = f"""Tailor this resume for the following job:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Type: {job.job_type or 'Full-time'}

JOB DESCRIPTION:
{job.description[:4000]}
{suggestions}

BASE RESUME (LaTeX):
{base_tex}

Return the COMPLETE tailored LaTeX source. Start with \\documentclass and end with \\end{{document}}."""

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
                system=TAILOR_SYSTEM_PROMPT,
                n_generators=2,
                n_critics=1,
                task_description=f"Tailor LaTeX resume for {job.title} at {job.company}",
                temperature=0.3,
                cache_extra=resume_hash,
            )
            job.tailoring_provider = "council"
            job.tailoring_model = "consensus"
        else:
            info = ai_client.complete_with_info(
                prompt=user_prompt,
                system=TAILOR_SYSTEM_PROMPT,
                temperature=0.3,
                cache_extra=resume_hash,
            )
            tailored_tex = info["response"].strip()
            job.tailoring_provider = info["provider"]
            job.tailoring_model = info["model"]

        # Strip markdown code fences if present
        if tailored_tex.startswith("```"):
            lines = tailored_tex.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            tailored_tex = "\n".join(lines)

        # Sanity check: must start with \documentclass
        if not tailored_tex.startswith("\\documentclass"):
            start = tailored_tex.find("\\documentclass")
            if start >= 0:
                tailored_tex = tailored_tex[start:]
            else:
                logger.warning(f"Tailored resume for {job.company} doesn't look like LaTeX, using base")
                tailored_tex = base_tex

        # Ensure it ends with \end{document}
        if "\\end{document}" not in tailored_tex:
            tailored_tex += "\n\\end{document}"

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
                task_description=f"Tailor resume sections (JSON) for {job.title} at {job.company}",
                temperature=0.3,
                cache_extra=sections_hash,
            )
            job.tailoring_provider = "council"
            job.tailoring_model = "consensus"
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
