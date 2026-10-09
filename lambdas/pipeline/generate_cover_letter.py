import logging
import os
import re

import boto3

from ai_helper import ai_complete, council_complete, get_supabase
# The one plain-text -> LaTeX escaper in this CodeUri (lambdas/pipeline/). It
# escapes all ten specials and never double-escapes, so prose that already
# carries `\&` is left alone. Reused rather than copied: the local escaper this
# replaces handled four characters and a `$` in a letter broke the compile.
from parse_sections import _escape_tex
from utils.keyword_extractor import extract_keywords

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ── System prompt (mirrors cover_letter.py) ──────────────────────────────────

COVER_LETTER_SYSTEM_PROMPT = r"""You are writing a cover letter as a real person. Not an AI. A human engineer who gets straight to the point.

STRUCTURE (3-4 paragraphs, 250-350 words):

Paragraph 1 (3 sentences max): Connect YOUR experience to THEIR specific work. Do NOT describe what the company does — they already know. Do NOT say "I want to work as" or "I am applying for" — these are weak student phrases.
Good example: "Building reliable payment infrastructure at scale is exactly what I did at Clover for 3 years. The DevOps Engineer role caught my attention because your team owns the CI/CD pipeline for the entire platform."
Bad example: "SearchWorks is modernizing core payment platforms. I want to work as a DevOps Engineer."
Lead with the CONNECTION between your experience and their work, not a description of their company.

Paragraph 2 (6-8 sentences): This is the meat. Map TWO specific JD requirements to YOUR achievements:
- Quote or paraphrase a JD requirement, then connect it to a specific metric from your resume.
- Pattern: "Your team [JD requirement]. At Clover, I [specific achievement with metric]."
- Example: "Your team ships observability tooling at scale. At Clover, I built monitoring dashboards across 8 microservices that reduced MTTR by 35 percent."
- Use ONLY metrics that appear VERBATIM in the resume provided below. Do NOT invent, extrapolate, or round numbers.
- ALLOWED METRICS (these appear in the resume — use ONLY these, with CORRECT context):
  * "35%" = MTTR reduction (incident response time), NOT code quality
  * "85%" = release lead time reduction (3 days to 4-6 hours), NOT code quality or test coverage
  * "99.9%" = uptime SLA, NOT availability of anything else
  * "30%" = weekly report preparation time reduction, NOT deployment speed
  * "22%" = infrastructure cost reduction, NOT deployment speed
  * "8 production microservices" = services built at Clover
  * "3,000+ tests" = Jest test count, NOT pytest
  * "14 months" = time to promotion at Clover
  * "3 AWS regions" = multi-region deployment scope
  * "4-6 hours" = release lead time AFTER improvement (down from 3 days)
- Do NOT reattach a metric to a different achievement. "35%" is ALWAYS about MTTR, never about anything else.
- If a JD requirement doesn't map to a metric in the resume, describe the achievement WITHOUT a number.
- Do NOT list technologies. Show impact through specific stories.

Paragraph 3 (3-4 sentences): Mention ONE more relevant project by name (e.g., Purrrfect Keys, WhatsTheCraic) with a specific result. Say you are available and based in Dublin. End with a confident, forward-looking sentence. No begging.

VOICE:
- Write in first person. Vary sentence length. Some short. Some a bit longer to explain something specific.
- Sound like you are writing an email to someone you respect but do not know yet.
- You recently completed your MSc in Cloud Computing and have 3 years of industry experience at Clover IT Services (ended Jul 2024). You are NOT currently employed. Use past tense for work experience.

ABSOLUTE BANS (violating ANY of these means the letter is rejected):
- NO dashes of any kind. Not em-dashes. Not en-dashes. Not double hyphens. Use periods or commas instead.
- NO "I am excited", "I am writing to", "I believe", "I am confident", "I would welcome", "I look forward to", "I want to work as", "I am applying for"
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
    "i want to work as", "i am applying for", "will benefit from my",
    "contribute to the company", "in the next 14 months",
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


# Metrics that actually exist in the base resume — anything else is fabrication
_ALLOWED_METRICS = {
    "35", "85", "99.9", "30", "22", "66",  # percentages
    "8", "3000", "3,000", "14",  # counts
    "3",  # years, regions
}


def _check_metric_fabrication(text: str) -> list[str]:
    """Check if the cover letter uses specific percentages or numbers not in the base resume."""
    errors = []
    # Find all percentage claims like "42 percent", "42%", "68%"
    import re
    pct_matches = re.findall(r'(\d+(?:\.\d+)?)\s*(?:percent|%)', text.lower())
    for num in pct_matches:
        if num not in _ALLOWED_METRICS:
            errors.append(f"fabricated_metric: '{num}%' not in base resume (allowed: 35%, 85%, 99.9%, 30%, 22%, 66%)")

    return errors


def _letter_errors(text: str, company: str) -> list[str]:
    """Every check a letter is judged by, in one place.

    ONE builder for the first attempt AND every retry (CLAUDE.md #14). Until
    2026-10-08 the first attempt was judged by all three checks and each retry
    by `_validate_cover_letter` alone, so a retry claiming a metric the resume
    does not contain was "valid", replaced the first attempt and shipped.
    """
    return (_validate_cover_letter(text)["errors"]
            + _check_opening_quality(text, company)
            + _check_metric_fabrication(text))


def _check_opening_quality(text: str, company: str) -> list[str]:
    """Check that the cover letter doesn't open by describing the company generically."""
    errors = []
    first_sentence = text.split(".")[0].lower() if text else ""
    company_lower = company.lower()
    bad_patterns = [
        f"{company_lower} is a", f"{company_lower} specializes",
        f"{company_lower} is an", f"{company_lower} provides",
        f"{company_lower} offers", "a company that",
        "i want to work as", "i am writing to",
        "this project taught me", "these experiences have given me",
    ]
    for pattern in bad_patterns:
        if pattern in first_sentence:
            errors.append(f"bad_opening: '{pattern}' — open with what interests you about their WORK, not what the company IS")
    return errors


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

%(header)s

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
%(signature)s

\end{document}"""


# EXPLICIT FALLBACK, used only when the user's row cannot supply the header.
# These are the values this template hardcoded for every user until 2026-10-08
# (the repository owner's). They are used whole when there is no `users` row,
# and to fill gaps only for the owner's own row (matched by email) -- never to
# fill another user's missing phone or links with the owner's.
_OWNER_FALLBACK = {
    "name": "Utkarsh Singh",
    "email": "254utkarsh@gmail.com",
    "phone": "+353 892515620",
    "location": "Dublin, Ireland",
    "github": "https://github.com/UT07",
    "linkedin": "https://www.linkedin.com/in/utkarshsingh2001/",
}
_PROFILE_FIELDS = ("name", "email", "phone", "location", "github", "linkedin")


def _fetch_contact(db, user_id: str) -> dict:
    """The header fields for THIS user, from `users`, with the documented fallback."""
    row = None
    # PostgREST fails the whole select on one unknown column, so retry with the
    # two NOT-NULL-ish columns before giving up: falling back to the owner's
    # header for another user over a missing `github` column would be worse
    # than a header without links.
    for columns in (", ".join(_PROFILE_FIELDS), "name, email"):
        try:
            resp = db.table("users").select(columns).eq("id", user_id).limit(1).execute()
            row = resp.data[0] if resp.data else None
            break
        except Exception as exc:
            logger.warning("[cover_letter] could not read users(%s) for %s: %s",
                           columns, user_id, exc)
    if not row or not ((row.get("name") or "").strip() or (row.get("email") or "").strip()):
        logger.warning("[cover_letter] no usable profile for %s; using the owner "
                       "fallback header", user_id)
        return dict(_OWNER_FALLBACK)
    contact = {k: (row.get(k) or "").strip() for k in _PROFILE_FIELDS}
    if contact["email"].lower() == _OWNER_FALLBACK["email"]:
        contact = {k: contact[k] or _OWNER_FALLBACK[k] for k in _PROFILE_FIELDS}
    return contact


def _url(value: str) -> str:
    """A URL safe inside \\href's first argument: no braces, backslashes or spaces."""
    v = "".join(ch for ch in value if ch not in "{}\\ ")
    if v and not v.startswith(("http://", "https://")):
        v = "https://" + v
    return v.replace("%", r"\%").replace("#", r"\#")


def _render_header(contact: dict) -> str:
    """The centred contact block, with only the fields the user actually has."""
    line1 = [_escape_tex(x) for x in (contact.get("location"), contact.get("phone")) if x]
    if contact.get("email"):
        line1.append(r"\href{mailto:%s}{%s}" % (_url(contact["email"]).removeprefix("https://"),
                                                 _escape_tex(contact["email"])))
    links = []
    for key in ("github", "linkedin"):
        if contact.get(key):
            url = _url(contact[key])
            shown = url.removeprefix("https://").removeprefix("http://").removeprefix("www.")
            links.append(r"\href{%s}{%s}" % (url, _escape_tex(shown.rstrip("/"))))
    rows = [r"{\Large \textbf{%s}}" % _escape_tex(contact.get("name") or "")]
    for parts in (line1, links):
        if parts:
            rows.append(r" \textbar\ ".join(parts))
    # Same spacing the hardcoded header had: 0.3em under the name.
    body = rows[0] + ("\\\\[0.3em]\n" + "\\\\\n".join(rows[1:]) if rows[1:] else "")
    return "\\begin{center}\n" + body + "\n\\end{center}"


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]
    tailoring_depth = event.get("tailoring_depth")
    if tailoring_depth is None:
        # Backward-compat: old callers only set light_touch
        tailoring_depth = "light" if event.get("light_touch") else "moderate"
    light_touch = tailoring_depth == "light"

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    job_row = db.table("jobs_raw").select("*").eq("job_hash", job_hash).execute()
    if not job_row.data:
        return {"error": f"Job {job_hash} not found"}
    job = job_row.data[0]

    # Same shared accessor as tailor_resume and score_batch. A local limit(1)
    # here meant the letter could describe achievements from a document the
    # tailorer had rejected -- see shared.resume_format.fetch_tailorable_resume.
    from shared.resume_format import fetch_tailorable_resume

    base = fetch_tailorable_resume(db, user_id)
    if base.skipped:
        logger.warning(
            "[cover_letter] skipped %d newer resume(s) that are not LaTeX — writing "
            "from the same row the tailorer will use", base.skipped,
        )
    resume_tex = base.tex
    contact = _fetch_contact(db, user_id)

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
- Name: {contact.get('name') or ''}
- Location: {contact.get('location') or ''}
- Visa: Stamp 1G (authorized for full-time employment in Ireland)
- Fresh MSc Cloud Computing graduate with 2+ years industry experience
- Email: {contact.get('email') or ''}{keyword_hint}

Write ONLY the body paragraphs of the cover letter (3-4 paragraphs).
Do NOT include the header, date, salutation, or closing — I'll add those from my template.
Do NOT use any LaTeX commands in the body — just plain text paragraphs."""

    def _generate_body(prompt: str) -> tuple[str, str, str]:
        """Call AI and return (body_text, provider, model). ALWAYS uses council."""
        try:
            result = council_complete(
                prompt=prompt,
                system=COVER_LETTER_SYSTEM_PROMPT,
                task_description=(
                    f"Write cover letter for {job['title']} at {job['company']}. "
                    "Must open with something specific about their WORK, not describe the company. "
                    "Each paragraph must map a JD requirement to a specific resume achievement with a real metric."
                ),
                n_generators=2,
                temperature=0.7,
                task="cover_letter",
                # base_skills/base_body deliberately NOT forwarded: this
                # handler's output is plain prose paragraphs (the prompt
                # above explicitly forbids LaTeX), never a
                # \section*{Skills}-shaped document, so check_fabrication's
                # section regex can never match it -- base_skills would be a
                # documented no-op here, not a meaningful guard. base_body
                # would be actively wrong to pass: check_textbf_preservation
                # has no policy gate and runs unconditionally whenever
                # base_body is supplied, comparing \textbf{ counts between
                # the LaTeX base resume (many) and this prose body (always
                # zero) -- that would flag "textbf_stripped" on every single
                # cover letter regardless of quality.
            )
        except RuntimeError:
            result = ai_complete(prompt, system=COVER_LETTER_SYSTEM_PROMPT, temperature=0.7)
        return result["content"].strip(), result.get("provider", "council"), result.get("model", "consensus")

    # Generate with validation + retry (max 2 retries)
    body_text, provider, model = _generate_body(user_prompt)
    best_body, best_provider, best_model = body_text, provider, model
    best_errors: list[str] = []

    company = job.get("company", "")
    errors = _letter_errors(body_text, company)
    if errors:
        best_errors = errors
        logger.warning(f"[cover_letter] Validation failed for {job_hash}: {errors}")

        for retry in range(2):
            correction_lines = "\n".join(f"- FIX: {e}" for e in errors)
            retry_prompt = (
                user_prompt
                + f"\n\nYour previous attempt had these problems:\n{correction_lines}"
                + "\nPlease fix ALL of them in this attempt. Return ONLY the corrected body paragraphs."
            )
            body_text, provider, model = _generate_body(retry_prompt)
            errors = _letter_errors(body_text, company)

            if not errors:
                best_body, best_provider, best_model = body_text, provider, model
                best_errors = []
                logger.info(f"[cover_letter] Validation passed on retry {retry + 1} for {job_hash}")
                break
            elif len(errors) < len(best_errors):
                best_body, best_provider, best_model = body_text, provider, model
                best_errors = errors
                logger.warning(f"[cover_letter] Retry {retry + 1} still invalid for {job_hash}: {errors}")
            else:
                logger.warning(f"[cover_letter] Retry {retry + 1} no improvement for {job_hash}: {errors}")

    if best_errors:
        logger.warning(f"[cover_letter] Accepting best attempt for {job_hash} with issues: {best_errors}")

    # Every LaTeX special in the prose, the job fields and the profile values.
    full_tex = COVER_LETTER_TEMPLATE % {
        "header": _render_header(contact),
        "signature": _escape_tex(contact.get("name") or ""),
        "company_name": _escape_tex(company),
        "job_title": _escape_tex(job.get("title", "")),
        "body": _escape_tex(best_body),
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
        # What is still wrong with the shipped letter, by the same builder that
        # judged every attempt. Empty means it passed all of them.
        "validation_errors": best_errors,
    }
