"""Per-question answer generation routed by category.

Spec reference: docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md §7.3 step 9.

Standard fields (first_name, email, etc.) come from the profile dict.
File fields (resume, cover_letter) return file markers consumed at submit time.
EEO/confirmation/marketing/referral skip AI per spec.
Custom questions go through ai_complete_cached with temperature=0.3,
max_tokens=300, cache_hours=24*7 (per spec §7.3).
"""
from __future__ import annotations

from typing import Callable, Optional
from difflib import get_close_matches

from shared.question_classifier import classify_question


_STANDARD_FIELD_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin",
    "github": "github",
    "website": "website",
    "location": "location",
}

_DECLINE_PATTERNS = (
    "decline", "prefer not", "rather not", "i don't wish", "do not wish",
)


# Spec §7.3 lines 666-671 — verbatim default candidate context
DEFAULT_CANDIDATE_CONTEXT = (
    "3+ years full-stack software engineering experience. MSc in Cloud Computing (ATU). "
    "AWS Solutions Architect Professional certified. Strong in Python (FastAPI, Flask, "
    "Django), TypeScript/React, AWS (ECS/Fargate, Lambda, RDS, S3, API Gateway), "
    "CI/CD, Docker, Kubernetes, Terraform. Track record of reducing MTTR 35%, "
    "cutting release lead time 85%, maintaining 99.9% uptime."
)


_SYSTEM_PROMPT = (
    "You are a job applicant filling out an application form. Answer concisely, "
    "truthfully, and in a way that presents the candidate positively. "
    "If the question is a dropdown/select, you MUST pick one of the provided "
    "options verbatim. Return ONLY the answer text, no explanation."
)


# Spec §7.3 lines 624-661 — verbatim user prompt template
_USER_PROMPT_TEMPLATE = """\
You are filling out a job application for {first_name} {last_name}, applying to {title} at {company}.

CANDIDATE PROFILE:
{candidate_context}

CONTACT:
- LinkedIn: {linkedin}
- GitHub: {github}
- Website: {website}
- Location: {location}
- Visa status: {visa_status}

PREFERENCES:
- Salary expectations: {salary}
- Notice period: {notice_period}

JOB CONTEXT:
- Role: {title}
- Company: {company}
- Location: {job_location}
- Description: {description}
- Key matches: {key_matches}

WORK AUTHORIZATION MATCHING:
- If the question asks about work authorization in a specific country, use:
  {work_authorizations}
- For "Remote - Europe" or "EU" locations, default to Ireland ("IE").
- For ambiguous locations, default to Ireland.

QUESTION: {question_label}
TYPE: {question_type}
{options_line}REQUIRED: {required}

Answer the question concisely, truthfully, and in a way that presents the candidate positively. \
Reference specific things from the job description when relevant. If the question is a \
dropdown/select, you MUST pick one of the provided options verbatim.

Return ONLY the answer text, no explanation."""


def generate_answer(
    question: dict,
    profile: dict,
    job: dict,
    resume_text: str,
    cover_letter_text: Optional[str],
    ai_complete_cached_fn: Callable[..., dict],
) -> dict:
    """Generate an answer for a single application question.

    Args:
        question: normalized question dict (label, field_name, type, required, options, description, category?)
        profile: user profile dict (must include candidate_context, work_authorizations, etc.)
        job: job dict (title, company, location, description, key_matches)
        resume_text: plaintext resume excerpt
        cover_letter_text: plaintext cover letter (optional)
        ai_complete_cached_fn: the lambdas.pipeline.ai_helper.ai_complete_cached function
                               (injected for testability)

    Returns:
        {"answer": str|bool|None, "category": str, "requires_user_action": bool}
    """
    field_name = question.get("field_name", "")
    qtype = question.get("type", "text")
    label = question.get("label", "")
    description = question.get("description")
    options = question.get("options") or []

    # File fields: marker consumed at submit time
    if qtype == "file":
        if "resume" in field_name.lower() or "resume" in label.lower():
            return {"answer": "<resume_pdf>", "category": "file", "requires_user_action": False}
        if "cover" in field_name.lower() or "cover" in label.lower():
            return {"answer": "<cover_letter_pdf>", "category": "file", "requires_user_action": False}
        return {"answer": None, "category": "file", "requires_user_action": True}

    # Standard fields from profile
    if field_name in _STANDARD_FIELD_MAP:
        return {
            "answer": profile.get(_STANDARD_FIELD_MAP[field_name], ""),
            "category": "standard",
            "requires_user_action": False,
        }

    # Honor pre-tagged category from fetcher (compliance / demographic_questions)
    category = question.get("category") or classify_question(label, description)

    if category == "eeo":
        decline = _find_decline_option(options)
        return {
            "answer": decline or (options[0] if options else None),
            "category": "eeo",
            "requires_user_action": False,
        }

    if category == "confirmation" or qtype == "checkbox":
        return {"answer": False, "category": "confirmation", "requires_user_action": True}

    if category == "marketing":
        no_option = next((o for o in options if o.lower() in ("no", "false", "unsubscribe")), None)
        return {
            "answer": no_option or False,
            "category": "marketing",
            "requires_user_action": False,
        }

    if category == "referral":
        default = profile.get("default_referral_source", "")
        match = _fuzzy_match(default, options) if default and options else None
        return {
            "answer": match or (options[0] if options else default),
            "category": "referral",
            "requires_user_action": False,
        }

    # Custom: AI generation with rich spec-compliant prompt
    options_line = f"OPTIONS: {options}\n" if options else ""
    prompt = _USER_PROMPT_TEMPLATE.format(
        first_name=profile.get("first_name", ""),
        last_name=profile.get("last_name", ""),
        title=job.get("title", ""),
        company=job.get("company", ""),
        candidate_context=profile.get("candidate_context") or DEFAULT_CANDIDATE_CONTEXT,
        linkedin=profile.get("linkedin", ""),
        github=profile.get("github", ""),
        website=profile.get("website", ""),
        location=profile.get("location", ""),
        visa_status=profile.get("visa_status", ""),
        salary=profile.get("salary_expectation_notes") or "Open to discussion, targeting competitive market rate",
        notice_period=profile.get("notice_period_text", ""),
        job_location=job.get("location", ""),
        description=(job.get("description") or "")[:2000],
        key_matches=job.get("key_matches", []),
        work_authorizations=profile.get("work_authorizations", {}),
        question_label=label,
        question_type=qtype,
        options_line=options_line,
        required=question.get("required", False),
    )

    result = ai_complete_cached_fn(
        prompt=prompt,
        system=_SYSTEM_PROMPT,
        temperature=0.3,
        max_tokens=300,
        cache_hours=24 * 7,
    )
    raw_answer = (result.get("content") or "").strip()

    # Post-process per spec §7.3 step 9
    if qtype == "yes_no":
        if raw_answer.lower() not in ("yes", "no"):
            raw_answer = "Yes"  # Safer default
    elif qtype in ("select", "multi_select") and options:
        if raw_answer not in options:
            match = _fuzzy_match(raw_answer, options)
            raw_answer = match or options[0]

    return {"answer": raw_answer, "category": "custom", "requires_user_action": False}


def _find_decline_option(options: list[str]) -> Optional[str]:
    for opt in options:
        if any(p in opt.lower() for p in _DECLINE_PATTERNS):
            return opt
    return None


def _fuzzy_match(query: str, options: list[str]) -> Optional[str]:
    if not query or not options:
        return None
    # Exact match (case-insensitive)
    for opt in options:
        if opt.lower() == query.lower():
            return opt
    # Numeric range match: extract a number from query and find the option whose range contains it
    import re
    nums = re.findall(r'\d+', query)
    if nums:
        n = int(nums[-1])
        for opt in options:
            range_nums = re.findall(r'\d+', opt)
            if len(range_nums) == 2:
                lo, hi = int(range_nums[0]), int(range_nums[1])
                if lo <= n <= hi:
                    return opt
            elif len(range_nums) == 1:
                threshold = int(range_nums[0])
                if '+' in opt and n >= threshold:
                    return opt
    # Difflib close match
    matches = get_close_matches(query, options, n=1, cutoff=0.4)
    if matches:
        return matches[0]
    # Substring fallback — only if query uniquely appears in one option
    substring_hits = [opt for opt in options if query.lower() in opt.lower()]
    if len(substring_hits) == 1:
        return substring_hits[0]
    # Reverse substring: option appears in query
    rev_hits = [opt for opt in options if opt.lower() in query.lower()]
    if len(rev_hits) == 1:
        return rev_hits[0]
    return None
