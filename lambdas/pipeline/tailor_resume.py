import logging
import os
import re

import boto3

from ai_helper import ai_complete, get_supabase

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# LaTeX preamble/body split + validation (mirrored from tailorer.py to keep
# the Lambda self-contained; SAM does not bundle the top-level tailorer.py).
# ---------------------------------------------------------------------------

_MACRO_ARITIES: dict[str, int] = {
    "projectentryurl": 5,
    "projectentry": 3,
    "jobentry": 4,
}


def _split_tex(tex: str) -> tuple[str, str]:
    """Split LaTeX source into (preamble, body). Preamble excludes \\begin{document}."""
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
    return f"{preamble}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


def _count_macro_args(tex: str, start: int) -> int:
    """Count consecutive balanced {...} groups starting at `start`."""
    i = start
    count = 0
    while i < len(tex):
        while i < len(tex) and tex[i].isspace():
            i += 1
        if i >= len(tex) or tex[i] != "{":
            break
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


def _validate_macro_arities(tex: str) -> list[str]:
    issues: list[str] = []
    for macro, expected in _MACRO_ARITIES.items():
        pattern = re.compile(r"\\" + macro + r"(?![a-zA-Z])")
        for match in pattern.finditer(tex):
            # Skip macro definitions: \newcommand{\jobentry}[4]{...}
            if match.start() > 0 and tex[match.start() - 1] == "{":
                continue
            actual = _count_macro_args(tex, match.end())
            if actual != expected:
                issues.append(f"\\{macro} has {actual} args (expected {expected})")
    return issues


_REQUIRED_SECTIONS = ["experience", "skills", "education"]

# Header markers that MUST survive tailoring — contact info is essential.
_HEADER_MARKERS = ["Utkarsh Singh", "254utkarsh@gmail.com"]


def _check_header_present(tex: str) -> list[str]:
    """Return list of header markers missing from the tex. Empty = all present."""
    return [m for m in _HEADER_MARKERS if m not in tex]


def _check_required_sections(tex: str) -> list[str]:
    """Return the list of required section keywords NOT found in any \\section heading.

    Uses substring matching so "Work Experience" satisfies "experience" and
    "Technical Skills" satisfies "skills". Matches the downstream compiler gate.
    """
    heads = [h.lower() for h in re.findall(r"\\section\*?\{([^}]*)\}", tex)]
    return [s for s in _REQUIRED_SECTIONS if not any(s in h for h in heads)]


def _check_brace_balance(tex: str) -> bool:
    """Return True if {/} are balanced (ignoring \\{ and \\})."""
    depth = 0
    i = 0
    while i < len(tex):
        if tex[i] == "\\" and i + 1 < len(tex) and tex[i + 1] in "{}":
            i += 2
            continue
        if tex[i] == "{":
            depth += 1
        elif tex[i] == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


_SYSTEM_PROMPT = r"""You are an expert resume writer who tailors technical resumes for specific job listings. You work with LaTeX resume BODIES (the content between \begin{document} and \end{document} only).

CRITICAL OUTPUT RULE:
Return ONLY the tailored body. Do NOT emit \documentclass, \usepackage, \newcommand, \begin{document}, or \end{document}. The base preamble is managed separately.

KEEP THE HEADER BLOCK VERBATIM (must appear at the top of your output — name, phone, email, GitHub, LinkedIn, portfolio links. You MAY only change the \normalsize role title line):
  \begin{center}
  {\Large \textbf{Utkarsh Singh}}\\[0.04em]
  {\normalsize <role title with tech>}\\[0.08em]
  Dublin, Ireland \textbar\ +353 892515620 \textbar\ \href{mailto:254utkarsh@gmail.com}{254utkarsh@gmail.com}\\[0.08em]
  \href{https://github.com/UT07}{github.com/UT07} \textbar\ \href{https://www.linkedin.com/in/utkarshsingh2001/}{linkedin.com/in/utkarshsingh2001} \textbar\ \href{https://utworld.netlify.app}{utworld.netlify.app}
  \end{center}

CUSTOM MACROS (already defined — use with EXACT argument counts):
- \jobentry{company}{location}{dates}{title}       — 4 args
- \projectentry{name}{dates}{tech}                 — 3 args
- \projectentryurl{name}{dates}{url}{url-text}{tech} — 5 args

Each macro call MUST be followed by a \begin{itemize}...\end{itemize} block. Do NOT put \begin{itemize} inside the macro call.

RULES:
1. NEVER fabricate experience. Reword, reorder, emphasize what already exists.
2. The resume must remain truthful.
3. Return ONLY the body — no preamble, no markdown fences."""

_LIGHT_TOUCH_NOTE = (
    "TAILORING DEPTH: LIGHT TOUCH — make minimal edits: reorder skills to match JD "
    "keywords, tweak the summary sentence. Keep 95%+ of the body unchanged."
)
_FULL_REWRITE_NOTE = (
    "TAILORING DEPTH: FULL REWRITE — rewrite bullet points to emphasize relevant "
    "experience. Reorder sections strategically within the body."
)


def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]
    light_touch = event.get("light_touch", False)

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    # Read job from jobs_raw
    job = db.table("jobs_raw").select("*").eq("job_hash", job_hash).execute()
    if not job.data:
        return {"error": f"Job {job_hash} not found"}
    job = job.data[0]

    # Get latest resume (no is_active column; use most recently created)
    resume = db.table("user_resumes").select("*").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(1).execute()
    if not resume.data:
        return {"error": "No resume found"}
    base_tex = resume.data[0].get("tex_content", "")

    # Split base: AI only sees the body, never the preamble.
    base_preamble, base_body = _split_tex(base_tex)
    if not base_preamble:
        logger.error(f"[tailor] base resume missing \\begin{{document}} for user {user_id}")
        return {"error": "base resume has no \\begin{document}"}

    # Tailor using AI (body-only prompt)
    depth_note = _LIGHT_TOUCH_NOTE if light_touch else _FULL_REWRITE_NOTE
    system_prompt = f"{depth_note}\n\n{_SYSTEM_PROMPT}"

    user_prompt = f"""Tailor this resume body for the following job.

Job: {job['title']} at {job['company']}
Description: {job.get('description', '')[:3000]}

BASE RESUME BODY:
{base_body}

Return ONLY the tailored body. No \\documentclass, no \\newcommand, no \\begin{{document}}."""

    response_dict = ai_complete(user_prompt, system=system_prompt)
    ai_response = response_dict["content"]

    # Extract body: AI may return a full doc despite instructions, or just a body.
    if "\\begin{document}" in ai_response:
        _ignored, ai_body = _split_tex(ai_response)
    else:
        ai_body = ai_response.strip().removesuffix("\\end{document}").strip()

    if not ai_body:
        logger.error(f"[tailor] AI returned empty body for job {job_hash}")
        return {"error": "AI returned empty body"}

    # Splice: base preamble (known-good) + AI body + \end{document}
    tailored_tex = _splice_tex(base_preamble, ai_body)

    # Hard gates: fall back to base_tex on validation failure
    validation_errors = []
    if not _check_brace_balance(tailored_tex):
        validation_errors.append("brace imbalance")
    arity_issues = _validate_macro_arities(tailored_tex)
    if arity_issues:
        validation_errors.append(f"arity: {'; '.join(arity_issues[:3])}")
    missing = _check_required_sections(tailored_tex)
    if missing:
        validation_errors.append(f"missing sections: {missing}")
    missing_header = _check_header_present(tailored_tex)
    if missing_header:
        validation_errors.append(f"missing header markers: {missing_header}")

    if validation_errors:
        logger.warning(
            f"[tailor] validation failed for {job_hash}: {'; '.join(validation_errors)} "
            f"— falling back to base resume"
        )
        tailored_tex = base_tex

    # Write to S3
    tex_key = f"users/{user_id}/resumes/{job_hash}_tailored.tex"
    s3.put_object(Bucket=bucket, Key=tex_key, Body=tailored_tex.encode("utf-8"))

    # Update job record
    db.table("jobs").update({
        "resume_version": 1,
        "tailoring_model": f"{response_dict.get('provider', 'council')}:{response_dict.get('model', 'consensus')}",
    }).eq("user_id", user_id).eq("job_hash", job_hash).execute()

    logger.info(
        f"[tailor] {'Light' if light_touch else 'Full'} tailor for {job_hash} "
        f"({'fallback' if validation_errors else 'ok'})"
    )
    return {
        "job_hash": job_hash,
        "tex_s3_key": tex_key,
        "user_id": user_id,
        "used_fallback": bool(validation_errors),
    }
