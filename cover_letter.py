"""Cover letter generator using multi-provider AI client.

Generates professional, tailored cover letters in LaTeX format.
"""

from __future__ import annotations
import logging
from pathlib import Path
from scrapers.base import Job
from ai_client import AIClient

logger = logging.getLogger(__name__)


COVER_LETTER_SYSTEM_PROMPT = r"""You are an expert cover letter writer for software engineering and DevOps/SRE roles. You write concise, compelling cover letters that get interviews.

RULES:
1. Keep it to 3-4 paragraphs, under one page.
2. Opening paragraph: Hook — mention the specific role, company, and ONE compelling reason you're a great fit.
3. Middle paragraph(s): Connect 2-3 specific achievements from the resume to the job's requirements. Use metrics.
4. Closing paragraph: Express enthusiasm, mention availability, and include a forward-looking statement.
5. Tone: Professional but personable. Not robotic. Show genuine interest in the company.
6. NEVER fabricate anything. Only reference real experience from the resume.
7. Address the Stamp 1G visa status naturally if relevant (e.g., "I am based in Dublin and authorized for full-time employment in Ireland").
8. Use the candidate's actual contact details.

Return ONLY the body paragraphs of the cover letter (3-4 paragraphs of plain text).
Do NOT include any LaTeX commands, headers, dates, or closings — just the body text."""


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


def generate_cover_letter(
    job: Job,
    resume_tex: str,
    ai_client: AIClient,
    output_dir: Path,
) -> str:
    """Generate a tailored cover letter for a specific job.

    Returns the path to the cover letter .tex file.
    """
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
- Name: Utkarsh Singh
- Location: Dublin, Ireland
- Visa: Stamp 1G (authorized for full-time employment in Ireland)
- Fresh MSc Cloud Computing graduate with 2+ years industry experience
- Email: 254utkarsh@gmail.com

Write ONLY the body paragraphs of the cover letter (3-4 paragraphs).
Do NOT include the header, date, salutation, or closing — I'll add those from my template.
Do NOT use any LaTeX commands in the body — just plain text paragraphs."""

    try:
        body_text = ai_client.complete(
            prompt=user_prompt,
            system=COVER_LETTER_SYSTEM_PROMPT,
            temperature=0.7,
            skip_cache=True,  # Cover letters should be unique each time
        )
        body_text = body_text.strip()

        # Escape LaTeX special characters in the body
        body_text = body_text.replace("&", r"\&")
        body_text = body_text.replace("%", r"\%")
        body_text = body_text.replace("#", r"\#")

        # Build the full LaTeX document
        company_escaped = job.company.replace("&", r"\&").replace("%", r"\%")
        title_escaped = job.title.replace("&", r"\&").replace("%", r"\%")

        full_tex = COVER_LETTER_TEMPLATE.format(
            company_name=company_escaped,
            job_title=title_escaped,
            body=body_text,
        )

        # Save cover letter .tex file
        safe_company = "".join(c for c in job.company if c.isalnum() or c in " _-")[:30].strip()
        safe_title = "".join(c for c in job.title if c.isalnum() or c in " _-")[:30].strip()
        filename = f"coverletter_{safe_company}_{safe_title}".replace(" ", "_")
        tex_path = output_dir / f"{filename}.tex"
        tex_path.write_text(full_tex, encoding="utf-8")

        job.cover_letter_tex_path = str(tex_path)
        logger.info(f"[COVER LETTER] {job.title} @ {job.company} -> {tex_path.name}")
        return str(tex_path)

    except Exception as e:
        logger.error(f"Error generating cover letter for {job.company}: {e}")
        return ""
