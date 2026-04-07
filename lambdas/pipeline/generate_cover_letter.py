import logging
import os
import re

import boto3

from ai_helper import ai_complete, council_complete, get_supabase
from utils.keyword_extractor import extract_keywords

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ── System prompt (mirrors cover_letter.py) ──────────────────────────────────

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


# ── Validation (mirrors cover_letter.py) ─────────────────────────────────────

BANNED_PHRASES = [
    "i am excited", "leverage", "passionate", "synergy", "aligns with",
    "keen to", "eager to", "i am writing to", "thrilled", "delighted",
    "dynamic team", "proven track record", "highly motivated", "self-motivated",
    "results-driven", "detail-oriented", "strong background",
    "i am confident", "i would welcome", "i look forward to",
]

DASH_PATTERN = re.compile(r"[–—]|--")


def _validate_cover_letter(text: str) -> dict:
    """Return {valid: bool, errors: list, word_count: int}."""
    errors = []
    word_count = len(text.split())

    if word_count < 280 or word_count > 380:
        errors.append(f"word_count: {word_count} (expected 280-380)")

    text_lower = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in text_lower:
            errors.append(f"banned_phrase: '{phrase}'")

    if DASH_PATTERN.search(text):
        errors.append("dashes: em-dash, en-dash, or double hyphen found")

    return {"valid": len(errors) == 0, "errors": errors, "word_count": word_count}


# ── LaTeX template ────────────────────────────────────────────────────────────

COVER_LETTER_TEMPLATE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[top=1in,bottom=1in,left=1in,right=1in]{geometry}
\usepackage[hidelinks]{hyperref}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.8em}

\begin{document}

\begin{center}
{\Large \textbf{Utkarsh Singh}}\\[0.3em]
Dublin, Ireland \textbar\ +353 892515620 \textbar\ \href{mailto:254utkarsh@gmail.com}{254utkarsh@gmail.com}\\
\href{https://github.com/UT07}{github.com/UT07} \textbar\ \href{https://www.linkedin.com/in/utkarshsingh2001/}{linkedin.com/in/utkarshsingh2001}
\end{center}

\vspace{0.5em}
\hrule
\vspace{1em}

\today

\vspace{0.8em}

%(company_name)s Hiring Team\\
Re: %(job_title)s

\vspace{0.8em}

%(body)s

\vspace{0.8em}

Best regards,\\
Utkarsh Singh

\end{document}"""


def _escape_latex(text: str) -> str:
    """Escape characters that are special in LaTeX."""
    return (
        text.replace("&", r"\&")
            .replace("%", r"\%")
            .replace("#", r"\#")
            .replace("_", r"\_")
    )


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]
    light_touch = event.get("light_touch", False)

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    job_row = db.table("jobs_raw").select("*").eq("job_hash", job_hash).execute()
    if not job_row.data:
        return {"error": f"Job {job_hash} not found"}
    job = job_row.data[0]

    resume = db.table("user_resumes").select("*").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(1).execute()
    resume_tex = resume.data[0].get("tex_content", "") if resume.data else ""

    description = job.get("description", "") or ""

    # Extract top keywords from JD so the cover letter references key requirements
    jd_keywords = extract_keywords(description, max_keywords=8)
    keyword_hint = ""
    if jd_keywords:
        keyword_hint = (
            "\n\nKEY JD REQUIREMENTS (naturally weave these into the letter where relevant, "
            "do NOT list them):\n" + ", ".join(jd_keywords)
        )

    user_prompt = f"""Write a cover letter for this job application:

JOB LISTING:
- Title: {job['title']}
- Company: {job['company']}
- Location: {job.get('location', '')}

JOB DESCRIPTION:
{description[:3000]}

CANDIDATE'S RESUME (for reference — use real details only):
{resume_tex[:4000]}

CANDIDATE INFO:
- Name: Utkarsh Singh
- Location: Dublin, Ireland
- Visa: Stamp 1G (authorized for full-time employment in Ireland)
- Fresh MSc Cloud Computing graduate with 2+ years industry experience
- Email: 254utkarsh@gmail.com{keyword_hint}

Write ONLY the body paragraphs of the cover letter (3-4 paragraphs).
Do NOT include the header, date, salutation, or closing — I'll add those from my template.
Do NOT use any LaTeX commands in the body — just plain text paragraphs."""

    def _generate_body(prompt: str) -> tuple[str, str, str]:
        """Call AI and return (body_text, provider, model)."""
        if light_touch:
            result = ai_complete(prompt, system=COVER_LETTER_SYSTEM_PROMPT, temperature=0.7)
        else:
            try:
                result = council_complete(
                    prompt=prompt,
                    system=COVER_LETTER_SYSTEM_PROMPT,
                    task_description=f"Write cover letter body for {job['title']} at {job['company']}",
                    n_generators=2,
                    temperature=0.7,
                )
            except RuntimeError:
                result = ai_complete(prompt, system=COVER_LETTER_SYSTEM_PROMPT, temperature=0.7)
        return result["content"].strip(), result.get("provider", "council"), result.get("model", "consensus")

    # Generate with validation + retry (max 2 retries)
    body_text, provider, model = _generate_body(user_prompt)
    best_body, best_provider, best_model = body_text, provider, model
    best_errors: list[str] = []

    validation = _validate_cover_letter(body_text)
    if not validation["valid"]:
        best_errors = validation["errors"]
        logger.warning(f"[cover_letter] Validation failed for {job_hash}: {validation['errors']}")

        for retry in range(2):
            correction_lines = "\n".join(f"- FIX: {e}" for e in validation["errors"])
            retry_prompt = (
                user_prompt
                + f"\n\nYour previous attempt had these problems:\n{correction_lines}"
                + "\nPlease fix ALL of them in this attempt. Return ONLY the corrected body paragraphs."
            )
            body_text, provider, model = _generate_body(retry_prompt)
            validation = _validate_cover_letter(body_text)

            if validation["valid"]:
                best_body, best_provider, best_model = body_text, provider, model
                best_errors = []
                logger.info(f"[cover_letter] Validation passed on retry {retry + 1} for {job_hash}")
                break
            elif len(validation["errors"]) < len(best_errors):
                best_body, best_provider, best_model = body_text, provider, model
                best_errors = validation["errors"]
                logger.warning(f"[cover_letter] Retry {retry + 1} still invalid for {job_hash}: {validation['errors']}")
            else:
                logger.warning(f"[cover_letter] Retry {retry + 1} no improvement for {job_hash}: {validation['errors']}")

    if best_errors:
        logger.warning(f"[cover_letter] Accepting best attempt for {job_hash} with issues: {best_errors}")

    # Escape LaTeX special characters in body text
    body_escaped = (
        best_body
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("_", r"\_")
    )

    # Assemble the full LaTeX document
    company_escaped = _escape_latex(job.get("company", ""))
    title_escaped = _escape_latex(job.get("title", ""))

    full_tex = COVER_LETTER_TEMPLATE % {
        "company_name": company_escaped,
        "job_title": title_escaped,
        "body": body_escaped,
    }

    tex_key = f"users/{user_id}/cover_letters/{job_hash}_cover.tex"
    s3.put_object(Bucket=bucket, Key=tex_key, Body=full_tex.encode("utf-8"))

    logger.info(f"[cover_letter] Generated for {job_hash} via {best_provider}:{best_model}")
    return {
        "job_hash": job_hash,
        "tex_s3_key": tex_key,
        "user_id": user_id,
        "doc_type": "cover_letter",
        "provider": best_provider,
        "model": best_model,
    }
