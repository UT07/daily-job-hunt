import logging
import os
import re

import boto3

from ai_helper import ai_complete, council_complete, get_supabase

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


_REQUIRED_SECTIONS = ["experience", "skills", "education", "projects", "certifications"]

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
- Do NOT use filler phrases: "directly transferable to", "aligned with", "outcomes relevant to", "leveraging", "utilizing", "showcasing", "demonstrating proficiency in", "proven track record", "passionate about", "highly motivated", "self-motivated", "team player", "detail-oriented", "results-driven", "strong background in", "extensive experience in", "extensive experience", "seasoned professional", "experienced professional".
- Write short, direct sentences in active voice. Lead with the action verb.
- Do NOT append company-specific qualifiers to bullet points (e.g., "practices aligned with Company's GitOps patterns"). The bullet should stand on its own.
- Quantify impact with numbers and percentages where they already exist.
- Match job posting keywords by naturally weaving them into existing bullets, not by adding new sentences about them.

SUMMARY SECTION (CRITICAL):
- The summary MUST mention the specific role title from the JD (e.g., "Site Reliability Engineer", not just "engineer").
- The summary MUST include 1-2 specific metrics from the candidate's existing experience (e.g., "reduced MTTR by 35%", "maintained 99.9% uptime"). Use only metrics that appear in the base resume — do NOT fabricate numbers.
- The summary MUST reference at least 2 technologies that appear in BOTH the JD and the base resume.
- Do NOT open with generic phrases like "Highly motivated", "Experienced professional", "Strong background in", or "Passionate about". Lead with the role title or the candidate's most relevant qualification for THIS specific role.
- The summary should be 2-3 sentences. Do NOT write a paragraph.
- Every word must be specific to this job. A reader should not be able to swap this summary onto a different resume for a different role.

Return ONLY the tailored body content. No explanations, no markdown fences, no preamble commands."""

_LIGHT_TOUCH_NOTE = (
    "TAILORING DEPTH: LIGHT TOUCH — make minimal edits: reorder skills to match JD "
    "keywords, and rewrite the Summary to be specific to this role (follow SUMMARY "
    "SECTION rules: mention the exact role title, include 1-2 real metrics, reference "
    "2 technologies from the JD). Keep 95%+ of the body unchanged."
)
_MODERATE_NOTE = (
    "TAILORING DEPTH: MODERATE — rewrite bullet points to emphasize relevant "
    "experience, reorder sections strategically, but keep overall structure intact."
)
_FULL_REWRITE_NOTE = (
    "TAILORING DEPTH: FULL REWRITE — rewrite bullet points to emphasize relevant "
    "experience. Reorder sections strategically within the body."
)


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

    # Extract top keywords from JD for targeted tailoring
    from utils.keyword_extractor import extract_keywords
    description = job.get("description", "") or ""
    keywords = extract_keywords(description, max_keywords=15)
    keyword_section = ""
    if keywords:
        keyword_section = f"\n\nKEY JD REQUIREMENTS: {', '.join(keywords)}\nEnsure each of these is addressed in the resume. Weave them into existing bullets — do NOT add new fabricated experience."

    # Tailor using AI (body-only prompt)
    depth_note = {
        "light": _LIGHT_TOUCH_NOTE,
        "moderate": _MODERATE_NOTE,
        "heavy": _FULL_REWRITE_NOTE,
    }.get(tailoring_depth, _MODERATE_NOTE)
    system_prompt = f"{depth_note}\n\n{_SYSTEM_PROMPT}{keyword_section}"

    user_prompt = f"""Tailor this resume body for the following job.

Job: {job['title']} at {job['company']}
Description: {description[:4000]}

BASE RESUME BODY:
{base_body}

Return ONLY the tailored body. No \\documentclass, no \\newcommand, no \\begin{{document}}.

Reminder: your output MUST contain all six section headers verbatim: \\section*{{Summary}}, \\section*{{Technical Skills}}, \\section*{{Experience}}, \\section*{{Featured Projects}}, \\section*{{Education}}, \\section*{{Certifications}}."""

    # Light-touch jobs (score >= 85) only change ~5% of resume — single AI call is sufficient.
    # Full rewrites use council mode (2 generators + 1 critic) for quality.
    if light_touch:
        logger.info(f"[tailor] Light-touch mode for {job_hash} — single AI call")
        response_dict = ai_complete(user_prompt, system=system_prompt, temperature=0.3)
    else:
        try:
            response_dict = council_complete(
                prompt=user_prompt,
                system=system_prompt,
                task_description="Pick the resume that best matches the job description with complete sections.",
                n_generators=2,
                temperature=0.3,
            )
        except RuntimeError:
            response_dict = ai_complete(user_prompt, system=system_prompt)
    ai_response = response_dict["content"]

    # Strip markdown code fences (```latex, ```tex, etc.)
    ai_response = ai_response.strip()
    if "```" in ai_response:
        lines = ai_response.split("\n")
        filtered = []
        in_fence = False
        for line in lines:
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            filtered.append(line)
        ai_response = "\n".join(filtered)

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

    # Fix common AI LaTeX typos and unescaped special characters
    _TYPO_FIXES = {
        "\\emphergencystretch": "\\emergencystretch",
        "\\emergecystretch": "\\emergencystretch",
        "\\emergenystretch": "\\emergencystretch",
    }
    for typo, fix in _TYPO_FIXES.items():
        if typo in tailored_tex:
            tailored_tex = tailored_tex.replace(typo, fix)

    # Escape unescaped special characters in the BODY only (not preamble).
    # The preamble uses #1, #2 etc as macro parameters — escaping those breaks everything.
    import re
    body_start = tailored_tex.find(r"\begin{document}")
    if body_start > 0:
        preamble_part = tailored_tex[:body_start]
        body_part = tailored_tex[body_start:]
        body_part = re.sub(r'(?<!\\)#', r'\\#', body_part)  # C#, F# in body
        body_part = re.sub(r'(?<!\\)&(?!\\)', r'\\&', body_part)  # R&D, AT&T in body
        tailored_tex = preamble_part + body_part

    # Page length check: estimate body word count
    body_text = re.sub(r"\\[a-zA-Z]+\*?(\{[^}]*\})*", " ", ai_body)
    body_text = re.sub(r"[{}\\%&$#_^~]", " ", body_text)
    word_count = len(body_text.split())
    if word_count < 500:
        logger.warning(
            f"[tailor] body too short ({word_count} words) for {job_hash}, using base"
        )
        tailored_tex = base_tex

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
        f"[tailor] {tailoring_depth.capitalize()} tailor for {job_hash} "
        f"({'fallback' if validation_errors else 'ok'})"
    )
    return {
        "job_hash": job_hash,
        "tex_s3_key": tex_key,
        "user_id": user_id,
        "used_fallback": bool(validation_errors),
    }
