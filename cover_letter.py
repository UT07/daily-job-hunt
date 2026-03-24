"""Cover letter generator using multi-provider AI client.

Generates professional, tailored cover letters in LaTeX format.
"""

from __future__ import annotations
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING
from scrapers.base import Job
from ai_client import AIClient
import google_docs_client
from quality_logger import log_quality

if TYPE_CHECKING:
    from user_profile import UserProfile

logger = logging.getLogger(__name__)


COVER_LETTER_SYSTEM_PROMPT = r"""You are an expert cover letter writer for software engineering and DevOps/SRE roles. You write concise, compelling cover letters that get interviews.

RULES:
1. Keep it to 3-4 paragraphs, under one page.
2. Opening paragraph: Hook — mention the specific role, company, and ONE compelling reason you're a great fit.
3. Middle paragraph(s): Connect 2-3 specific achievements from the resume to the job's requirements. Use metrics.
4. Closing paragraph: Express enthusiasm, mention availability, and include a forward-looking statement.
5. Tone: Professional but personable. Not robotic. Show genuine interest in the company.
6. NEVER fabricate anything. Only reference real experience from the resume.
7. Address the visa/work-authorization status naturally if relevant (e.g., "I am based in Dublin and authorized for full-time employment in Ireland").
8. Use the candidate's actual contact details.

Return ONLY the body paragraphs of the cover letter (3-4 paragraphs of plain text).
Do NOT include any LaTeX commands, headers, dates, or closings — just the body text."""


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

        # Escape LaTeX special characters in the body
        body_text = body_text.replace("&", r"\&")
        body_text = body_text.replace("%", r"\%")
        body_text = body_text.replace("#", r"\#")

        # Build the full LaTeX document
        company_escaped = job.company.replace("&", r"\&").replace("%", r"\%")
        title_escaped = job.title.replace("&", r"\&").replace("%", r"\%")

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
        logger.info(f"[COVER LETTER] {job.title} @ {job.company} -> {tex_path.name} by {info['provider']}:{info['model']}")
        log_quality(task="cover_letter", provider=info["provider"], model=info["model"], job_id=job.job_id, company=job.company, job_title=job.title)
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

        logger.info(f"[COVER LETTER DOC] {job.title} @ {job.company} -> {result['doc_id']} by {info['provider']}:{info['model']}")
        log_quality(task="cover_letter", provider=info["provider"], model=info["model"], job_id=job.job_id, company=job.company, job_title=job.title)
        return result

    except Exception as e:
        logger.error(f"Error generating Google Docs cover letter for {job.company}: {e}")
        return {"doc_id": "", "doc_url": "", "pdf_path": ""}
