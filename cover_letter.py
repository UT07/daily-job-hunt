"""Cover letter generator using multi-provider AI client.

Generates professional, tailored cover letters in LaTeX format.
"""

from __future__ import annotations
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING
from scrapers.base import Job
from ai_client import AIClient
import google_docs_client
from quality_logger import log_quality
from utils.keyword_extractor import extract_keywords

if TYPE_CHECKING:
    from user_profile import UserProfile

logger = logging.getLogger(__name__)


# ── Cover letter validation ────────────────────────────────────────────

BANNED_PHRASES = [
    "i am excited", "leverage", "passionate", "synergy", "aligns with",
    "keen to", "eager to", "i am writing to", "thrilled", "delighted",
    "dynamic team",
]

DASH_PATTERN = re.compile(r"[–—]|--")


def validate_cover_letter(text: str) -> dict:
    """Validate cover letter content. Returns {valid: bool, errors: list, word_count: int}."""
    errors: List[str] = []
    words = text.split()
    word_count = len(words)

    if word_count < 280 or word_count > 380:
        errors.append(f"word_count: {word_count} (expected 280-380)")

    text_lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in text_lower:
            errors.append(f"banned_phrase: '{phrase}'")

    if DASH_PATTERN.search(text):
        errors.append("dashes: em-dash, en-dash, or double hyphen found")

    return {"valid": len(errors) == 0, "errors": errors, "word_count": word_count}


COVER_LETTER_SYSTEM_PROMPT = r"""You are writing a cover letter as a real person. Not an AI. A human engineer who gets straight to the point.

STRUCTURE (3-4 paragraphs, 250-350 words):

Paragraph 1 (3 sentences max): Open with something specific about the company. Not generic praise. Show you actually know what they do. Then state what role you want and your strongest qualification in one sentence.

Paragraph 2 (6-8 sentences): This is the meat. Tell a story about your most relevant work. Pick TWO achievements, one from work experience and one from projects. Use real numbers. "I built X which did Y resulting in Z." Connect each to what this team specifically needs. Do not list technologies. Show the impact and what you learned.

Paragraph 3 (3-4 sentences): Mention one more relevant skill or project briefly. Say you are available and based in Dublin. End with a confident forward-looking sentence. No begging.

VOICE:
- Write in first person. Vary sentence length. Some short. Some a bit longer to explain something specific.
- Sound like you are writing an email to someone you respect but do not know yet.
- You recently completed your MSc in Cloud Computing and have 3 years of industry experience at Clover IT Services (ended Jul 2024). You are NOT currently employed. Use past tense for work experience.

ABSOLUTE BANS (violating ANY of these means the letter is rejected):
- NO dashes of any kind. Not em-dashes. Not en-dashes. Not double hyphens. Use periods or commas instead.
- NO "I am excited", "I am writing to", "I believe", "I am confident", "I would welcome", "I look forward to"
- NO "leverage", "utilize", "passionate", "thrilled", "synergy", "aligns with", "keen to", "eager to"
- NO semicolons. Use periods.
- NO sentences starting with "With" or "As a"
- NO fewer than 280 words and NO more than 380 words
- NO dashes AT ALL. Replace every dash with a period or comma. This includes hyphens used as clause connectors.
- NO LaTeX commands or special characters (\, {, }, $, ^, ~). Write in plain English only.
- NO mentioning technologies the candidate has never used. Only reference skills from the resume.

SELF-CHECK before returning:
1. Count your words. If under 280 or over 380, revise.
2. Scan for any dash character (-, --, ---). If found, replace with a period or comma.
3. Scan for banned phrases. If found, rewrite the sentence.

Return ONLY the 3 body paragraphs as plain text. Nothing else."""


# ── Default (hardcoded) cover letter template — used when no UserProfile provided ──

COVER_LETTER_TEMPLATE = r"""\documentclass[10pt,a4paper]{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage{{lmodern}}
\usepackage[top=1in,bottom=1in,left=1in,right=1in]{{geometry}}
\usepackage[hidelinks]{{hyperref}}
\pagestyle{{empty}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0.8em}}

\begin{{document}}

\begin{{center}}
{{\Large \textbf{{Utkarsh Singh}}}}\\[0.3em]
Dublin, Ireland \textbar\ +353 892515620 \textbar\ \href{{mailto:254utkarsh@gmail.com}}{{254utkarsh@gmail.com}}\\
\href{{https://github.com/UT07}}{{github.com/UT07}} \textbar\ \href{{https://www.linkedin.com/in/utkarshsingh2001/}}{{linkedin.com/in/utkarshsingh2001}}
\end{{center}}

\vspace{{0.5em}}
\hrule
\vspace{{1em}}

\today

\vspace{{0.8em}}

{company_name} Hiring Team\\
Re: {job_title}

\vspace{{0.8em}}

{body}

\vspace{{0.8em}}

Best regards,\\
Utkarsh Singh

\end{{document}}"""

# ── Default candidate info block for cover letter prompts ──

_DEFAULT_CL_CANDIDATE_INFO = """\
- Name: Utkarsh Singh
- Location: Dublin, Ireland
- Visa: Stamp 1G (authorized for full-time employment in Ireland)
- Fresh MSc Cloud Computing graduate with 2+ years industry experience
- Email: 254utkarsh@gmail.com"""


def _escape_latex(text: str) -> str:
    """Escape characters that are special in LaTeX."""
    return text.replace("&", r"\&").replace("%", r"\%").replace("#", r"\#").replace("_", r"\_")


def _build_cover_letter_template(user: "UserProfile") -> str:
    """Build a LaTeX cover letter template with the user's contact info."""
    name_escaped = _escape_latex(user.name)
    location_escaped = _escape_latex(user.location) if user.location else ""
    phone_escaped = _escape_latex(user.phone) if user.phone else ""
    email_escaped = _escape_latex(user.email) if user.email else ""

    # Build contact line parts
    contact_parts = []
    if location_escaped:
        contact_parts.append(location_escaped)
    if phone_escaped:
        contact_parts.append(phone_escaped)
    if email_escaped:
        contact_parts.append(r"\href{{mailto:{email}}}{{{email_esc}}}".format(
            email=user.email, email_esc=email_escaped))

    contact_line = r" \textbar\ ".join(contact_parts)

    # Build links line parts
    link_parts = []
    if user.github:
        # Extract display name from URL (e.g., "github.com/UT07")
        gh_display = re.sub(r"https?://", "", user.github).rstrip("/")
        link_parts.append(r"\href{{{url}}}{{{display}}}".format(
            url=user.github, display=gh_display))
    if user.linkedin:
        li_display = re.sub(r"https?://www\.", "", user.linkedin).rstrip("/")
        link_parts.append(r"\href{{{url}}}{{{display}}}".format(
            url=user.linkedin, display=li_display))
    if user.website:
        ws_display = re.sub(r"https?://", "", user.website).rstrip("/")
        link_parts.append(r"\href{{{url}}}{{{display}}}".format(
            url=user.website, display=ws_display))

    links_line = r" \textbar\ ".join(link_parts)

    # Assemble the header block
    header_lines = [r"{{\Large \textbf{{{name}}}}}\\[0.3em]".format(name=name_escaped)]
    if contact_line:
        header_lines.append(contact_line)
    if links_line:
        # Add line break between contact and links
        header_lines[-1] += r"\\"
        header_lines.append(links_line)

    header_block = "\n".join(header_lines)

    return r"""\documentclass[10pt,a4paper]{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage{{lmodern}}
\usepackage[top=1in,bottom=1in,left=1in,right=1in]{{geometry}}
\usepackage[hidelinks]{{hyperref}}
\pagestyle{{empty}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0.8em}}

\begin{{document}}

\begin{{center}}
{header}
\end{{center}}

\vspace{{0.5em}}
\hrule
\vspace{{1em}}

\today

\vspace{{0.8em}}

{{company_name}} Hiring Team\\
Re: {{job_title}}

\vspace{{0.8em}}

{{body}}

\vspace{{0.8em}}

Best regards,\\
{name_plain}

\end{{document}}""".format(header=header_block, name_plain=name_escaped)


def _build_candidate_info(user: "UserProfile") -> str:
    """Build the CANDIDATE INFO block for cover letter AI prompts."""
    lines = []
    lines.append(f"- Name: {user.name}")
    if user.location:
        lines.append(f"- Location: {user.location}")
    if user.visa_status:
        lines.append(f"- Visa: {user.visa_status}")
    ctx = user.to_candidate_context()
    if ctx:
        lines.append(f"- {ctx}")
    if user.email:
        lines.append(f"- Email: {user.email}")
    return "\n".join(lines)


def generate_cover_letter(
    job: Job,
    resume_tex: str,
    ai_client: AIClient,
    output_dir: Path,
    user_profile: Optional["UserProfile"] = None,
) -> str:
    """Generate a tailored cover letter for a specific job.

    Parameters
    ----------
    user_profile:
        Optional UserProfile. When provided, the LaTeX template header, AI
        prompt candidate info, and output filename are derived from the
        profile. Pass ``None`` to preserve the original single-user behavior.

    Returns the path to the cover letter .tex file.
    """
    # Build candidate info and template based on user profile availability
    if user_profile is not None:
        candidate_info = _build_candidate_info(user_profile)
        cl_template = _build_cover_letter_template(user_profile)
        name_prefix = user_profile.safe_filename_prefix()
    else:
        candidate_info = _DEFAULT_CL_CANDIDATE_INFO
        cl_template = COVER_LETTER_TEMPLATE
        name_prefix = "Utkarsh_Singh"

    # Extract top keywords from JD so the cover letter references key requirements
    jd_keywords = extract_keywords(job.description, max_keywords=8)
    keyword_hint = ""
    if jd_keywords:
        keyword_hint = (
            "\n\nKEY JD REQUIREMENTS (naturally weave these into the letter where relevant, "
            "do NOT list them):\n" + ", ".join(jd_keywords)
        )

    user_prompt = f"""Write a cover letter for this job application:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Remote: {job.remote}

JOB DESCRIPTION:
{job.description[:3000]}

CANDIDATE'S RESUME (for reference — use real details only):
{resume_tex[:4000]}

CANDIDATE INFO:
{candidate_info}{keyword_hint}

Write ONLY the body paragraphs of the cover letter (3-4 paragraphs).
Do NOT include the header, date, salutation, or closing — I'll add those from my template.
Do NOT use any LaTeX commands in the body — just plain text paragraphs."""

    try:
        # Use council if available (3 models generate, 2 critique, best wins)
        use_council = hasattr(ai_client, 'council_complete') and len(getattr(ai_client, 'providers', [])) >= 3

        def _generate_body(prompt: str) -> str:
            """Generate cover letter body text via AI, returns raw text."""
            if use_council:
                logger.info(f"[COVER LETTER] Using council for {job.company}")
                text = ai_client.council_complete(
                    prompt=prompt,
                    system=COVER_LETTER_SYSTEM_PROMPT,
                    n_generators=2,
                    n_critics=1,
                    task_description=f"Write cover letter body for {job.title} at {job.company}",
                    temperature=0.7,
                    skip_cache=True,
                )
                job.cover_letter_provider = "council"
                job.cover_letter_model = "consensus"
            else:
                info = ai_client.complete_with_info(
                    prompt=prompt,
                    system=COVER_LETTER_SYSTEM_PROMPT,
                    temperature=0.7,
                    skip_cache=True,
                )
                text = info["response"].strip()
                job.cover_letter_provider = info["provider"]
                job.cover_letter_model = info["model"]
            return text

        # Generate with validation + retry (max 2 retries)
        body_text = _generate_body(user_prompt)
        best_body = body_text
        best_errors: List[str] = []

        validation = validate_cover_letter(body_text)
        if not validation["valid"]:
            best_errors = validation["errors"]
            logger.warning(
                f"[COVER LETTER] Validation failed for {job.company}: {validation['errors']}"
            )
            for retry in range(2):
                correction_lines = "\n".join(f"- FIX: {e}" for e in validation["errors"])
                retry_prompt = (
                    user_prompt
                    + f"\n\nYour previous attempt had these problems:\n{correction_lines}"
                    + "\nPlease fix ALL of them in this attempt. Return ONLY the corrected body paragraphs."
                )
                body_text = _generate_body(retry_prompt)
                validation = validate_cover_letter(body_text)

                if validation["valid"]:
                    best_body = body_text
                    best_errors = []
                    logger.info(
                        f"[COVER LETTER] Validation passed on retry {retry + 1} for {job.company}"
                    )
                    break
                else:
                    # Keep the attempt with fewer errors
                    if len(validation["errors"]) < len(best_errors):
                        best_body = body_text
                        best_errors = validation["errors"]
                    logger.warning(
                        f"[COVER LETTER] Retry {retry + 1} still invalid for {job.company}: "
                        f"{validation['errors']}"
                    )

        if best_errors:
            logger.warning(
                f"[COVER LETTER] Accepting best attempt for {job.company} with issues: {best_errors}"
            )

        body_text = best_body

        # Escape LaTeX special characters in the body text
        body_text = body_text.replace("&", r"\&")
        body_text = body_text.replace("%", r"\%")
        body_text = body_text.replace("#", r"\#")
        body_text = body_text.replace("_", r"\_")

        # Build the full LaTeX document
        company_escaped = _escape_latex(job.company)
        title_escaped = _escape_latex(job.title)

        full_tex = cl_template.format(
            company_name=company_escaped,
            job_title=title_escaped,
            body=body_text,
        )

        # Save cover letter .tex file
        safe_title = "".join(c for c in job.title if c.isalnum() or c in " _-")[:30].strip()
        safe_company = "".join(c for c in job.company if c.isalnum() or c in " _-")[:30].strip()
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{name_prefix}_{safe_title}_{safe_company}_{date_str}_CoverLetter".replace(" ", "_")
        tex_path = output_dir / f"{filename}.tex"
        tex_path.write_text(full_tex, encoding="utf-8")

        job.cover_letter_tex_path = str(tex_path)
        logger.info(f"[COVER LETTER] {job.title} @ {job.company} -> {tex_path.name} by {job.cover_letter_provider}:{job.cover_letter_model}")
        log_quality(task="cover_letter", provider=job.cover_letter_provider, model=job.cover_letter_model, job_id=job.job_id, company=job.company, job_title=job.title)
        return str(tex_path)

    except Exception as e:
        logger.error(f"Error generating cover letter for {job.company}: {e}")
        return ""


def generate_cover_letter_doc(
    job: Job,
    resume_tex: str,
    ai_client: AIClient,
    output_dir: Path,
    template_doc_id: str,
    share_with: str = "",
    credentials_path: str = "google_credentials.json",
    user_profile: Optional["UserProfile"] = None,
) -> Dict[str, str]:
    """Generate a tailored cover letter using a Google Docs template.

    Uses the same AI prompt as generate_cover_letter() to produce plain text body
    paragraphs, then populates a Google Doc template instead of producing LaTeX.

    Template placeholders expected: {{COMPANY_NAME}}, {{JOB_TITLE}}, {{BODY}}, {{DATE}}

    Parameters
    ----------
    user_profile:
        Optional UserProfile. When provided, the AI prompt candidate info and
        output filename are derived from the profile.

    Returns a dict: {"doc_id": ..., "doc_url": ..., "pdf_path": ...}
    """
    # Build candidate info and filename prefix
    if user_profile is not None:
        candidate_info = _build_candidate_info(user_profile)
        name_prefix = user_profile.safe_filename_prefix()
    else:
        candidate_info = _DEFAULT_CL_CANDIDATE_INFO
        name_prefix = "Utkarsh_Singh"

    user_prompt = f"""Write a cover letter for this job application:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Remote: {job.remote}

JOB DESCRIPTION:
{job.description[:3000]}

CANDIDATE'S RESUME (for reference — use real details only):
{resume_tex[:4000]}

CANDIDATE INFO:
{candidate_info}

Write ONLY the body paragraphs of the cover letter (3-4 paragraphs).
Do NOT include the header, date, salutation, or closing — I'll add those from my template.
Do NOT use any LaTeX commands in the body — just plain text paragraphs."""

    try:
        info = ai_client.complete_with_info(
            prompt=user_prompt,
            system=COVER_LETTER_SYSTEM_PROMPT,
            temperature=0.7,
            skip_cache=True,  # Cover letters should be unique each time
        )
        body_text = info["response"].strip()
        job.cover_letter_provider = info["provider"]
        job.cover_letter_model = info["model"]

        # Build output PDF path
        safe_title = "".join(c for c in job.title if c.isalnum() or c in " _-")[:30].strip()
        safe_company = "".join(c for c in job.company if c.isalnum() or c in " _-")[:30].strip()
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{name_prefix}_{safe_title}_{safe_company}_{date_str}_CoverLetter".replace(" ", "_")
        pdf_path = str(output_dir / f"{filename}.pdf")

        doc_title = f"Cover Letter – {job.title} at {job.company} ({date_str})"

        replacements = {
            "COMPANY_NAME": job.company,
            "JOB_TITLE": job.title,
            "BODY": body_text,
            "DATE": datetime.now().strftime("%B %d, %Y"),
        }

        result = google_docs_client.create_resume_doc(
            template_doc_id=template_doc_id,
            replacements=replacements,
            title=doc_title,
            output_pdf_path=pdf_path,
            share_with=share_with,
            credentials_path=credentials_path,
        )

        logger.info(f"[COVER LETTER DOC] {job.title} @ {job.company} -> {result['doc_id']} by {job.cover_letter_provider}:{job.cover_letter_model}")
        log_quality(task="cover_letter", provider=job.cover_letter_provider, model=job.cover_letter_model, job_id=job.job_id, company=job.company, job_title=job.title)
        return result

    except Exception as e:
        logger.error(f"Error generating Google Docs cover letter for {job.company}: {e}")
        return {"doc_id": "", "doc_url": "", "pdf_path": ""}
