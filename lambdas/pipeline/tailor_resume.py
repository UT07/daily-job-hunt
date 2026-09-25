import logging
import os
import re

import boto3

from ai_helper import ai_complete, council_complete, get_supabase

try:
    from retrieval.bullets import retrieve_evidence
except Exception:  # retrieval package absent in some deploy paths
    retrieve_evidence = None

# Output guards moved to guardrails/output_guards.py (Task 21). Re-exported
# here under their original underscore-prefixed names so existing callers in
# this module's handler() and existing test patch targets keep working
# unchanged. `guardrails` sits alongside this module under lambdas/pipeline/
# and resolves as a flat top-level package in every shape this Lambda
# actually runs in (pytest, zip Lambda) — no try/except needed, same as
# guardrails/input_guards.py's own imports.
from guardrails.output_guards import check_banned_phrases as _check_banned_phrases
from guardrails.output_guards import check_brace_balance as _check_brace_balance
from guardrails.output_guards import check_required_sections as _check_required_sections
from guardrails.output_guards import check_fabrication as _check_fabrication
from guardrails.output_guards import check_header_present as _check_header_present
from guardrails.output_guards import check_textbf_preservation as _check_textbf_preservation


class TailorError(Exception):
    """Raised when tailoring cannot produce a resume for this job.

    MUST be raised, never returned. Step Functions treats a returned dict as a
    SUCCESSFUL invocation, so returning {"error": ...} bypasses the Catch on the
    TailorResume state; the failure then surfaces in CompileResume as an
    unhandled JSONPath error on $.tailor_result.tex_s3_key, which fails the
    whole execution instead of just this one job in the Map.

    Raising lets the existing Catch route to SaveJobAfterError as designed.
    """


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


def _derive_header_markers(profile: dict | None) -> list[str]:
    """Return per-user header markers from the profile, with safe fallbacks.

    Priority for the name marker: full_name → name → first_name+last_name.
    Then email. If neither name nor email resolves, return [] — skip the
    header check entirely rather than fail every tailor.

    Schema note: prod `users` table has `name`, `first_name`, `last_name`,
    `email` but no `full_name` column. The full_name lookup is kept for
    forward-compatibility with future onboarding flows that might add it.

    Multi-tenant fix: was previously hardcoded
    `["Utkarsh Singh", "254utkarsh@gmail.com"]`; this caused validation
    failure → fallback to base resume for ANY user other than Utkarsh AND
    for Utkarsh whenever the AI rewrapped his name across lines.
    """
    if not profile:
        return []
    markers = []
    name = (profile.get("full_name") or profile.get("name") or "").strip()
    if not name:
        first = (profile.get("first_name") or "").strip()
        last = (profile.get("last_name") or "").strip()
        name = " ".join(p for p in (first, last) if p)
    if name:
        markers.append(name)
    email = (profile.get("email") or "").strip()
    if email:
        markers.append(email)
    return markers


# ---------------------------------------------------------------------------
# Archetype detection (career-ops methodology)
# ---------------------------------------------------------------------------

_ARCHETYPES = {
    "sre_devops": {
        "signals": ["SRE", "site reliability", "infrastructure", "terraform", "kubernetes",
                     "monitoring", "incident", "on-call", "uptime", "observability", "platform engineer"],
        "framing": "Emphasize reliability metrics (uptime, MTTR), infrastructure automation, monitoring dashboards, incident response.",
    },
    "backend": {
        "signals": ["backend", "API", "microservices", "distributed systems", "database",
                     "REST", "GraphQL", "server-side"],
        "framing": "Emphasize API design, data modeling, system architecture, performance optimization.",
    },
    "fullstack": {
        "signals": ["full-stack", "full stack", "frontend", "React", "Vue", "Angular",
                     "Node.js", "web application", "UI"],
        "framing": "Emphasize end-to-end ownership, responsive UI, API integration, deployment pipelines.",
    },
    "platform_cloud": {
        "signals": ["platform", "cloud engineer", "AWS", "GCP", "Azure", "CI/CD",
                     "deployment", "DevOps", "IaC", "CDK", "CloudFormation"],
        "framing": "Emphasize cloud architecture, cost optimization, CI/CD pipelines, infrastructure as code.",
    },
    "data": {
        "signals": ["data engineer", "ETL", "Spark", "analytics", "ML pipeline",
                     "data platform", "warehouse", "Airflow"],
        "framing": "Emphasize data pipelines, processing scale, data quality, ML infrastructure.",
    },
}


def _detect_archetype(title: str, description: str) -> tuple[str, str]:
    """Classify job into an archetype. Returns (name, framing_instruction)."""
    text = f"{title} {description}".lower()
    scores = {}
    for arch, config in _ARCHETYPES.items():
        scores[arch] = sum(1 for s in config["signals"] if s.lower() in text)
    best = max(scores, key=scores.get) if any(scores.values()) else "fullstack"
    return best, _ARCHETYPES[best]["framing"]


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
   - Skills: reorder CATEGORIES to put the most relevant first. PRESERVE ALL 8 CATEGORIES from the base resume — do NOT merge or drop any. Keep the parenthetical details (e.g., "AWS (EC2, ECS/Fargate, EKS, Lambda, RDS, S3, API Gateway, SQS/SNS, CloudFront, Route 53)"). You may reorder items within a category to front-load JD-relevant technologies
   - Experience bullets: reorder within each job; tweak wording to match the job listing's terminology
   - Projects: EXACTLY 3 PROJECTS. No more, no less.
     * ALWAYS KEEP BOTH "Purrrfect Keys" AND "NaukriBaba" — these two are
       the candidate's largest, most extensive projects and cover every
       domain (mobile/AI/Firebase + Python/AWS/SaaS). Tailoring strategy:
       reword bullets to emphasize the JD-relevant aspects, never remove.
     * SELECT 1 more from: WhatsTheCraic, Genomic Benchmarking, UTWorld —
       pick the single most relevant to the JD.
     * COMPLETELY DELETE the other unselected projects. Remove their
       \projectentry/\projectentryurl AND their \begin{itemize}...\end{itemize}
       blocks entirely. Do NOT leave empty project shells.
     * If you output 4 or 5 projects, the resume will overflow to 3 pages
       and be REJECTED.
     * Rewrite ALL 3 project descriptions to emphasize aspects matching
       the JD; the two pinned projects (Purrrfect Keys, NaukriBaba) get
       JD-tailored bullets but their identity, dates, and tech stack
       headers stay intact.
4. The resume must remain truthful.
5. PAGE LAYOUT (CRITICAL):
   - The resume MUST be exactly TWO PAGES. No more, no less.
   - Page 1: Header, Summary, Technical Skills, Clover IT Services (7 bullets), and Seattle Kraken (3 bullets).
   - Page 2: 3 selected Projects (Purrrfect Keys + NaukriBaba pinned + 1 other), Education, Certifications.
   - The base template inserts \clearpage before \section*{Featured Projects} so the section header always lands on page 2 — DO NOT remove that \clearpage from the body you return.
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

SUMMARY SECTION (CRITICAL — 4-6 lines, not a one-liner):
- The summary MUST mention the specific role title from the JD (e.g., "Site Reliability Engineer", not just "engineer").
- The summary MUST include 2-3 specific metrics from the candidate's existing experience (e.g., "reduced MTTR by 35%", "maintained 99.9% uptime", "cut release lead time by 85%"). Use only metrics that appear in the base resume — do NOT fabricate numbers.
- The summary MUST reference at least 3 technologies that appear in BOTH the JD and the base resume.
- REQUIRED ELEMENTS THAT MUST ALWAYS APPEAR IN THE SUMMARY (adapt wording to the role, but never drop these):
  * \textbf{3+ years} of experience (or "3+ years" in some form)
  * \textbf{MSc Cloud Computing}
  * \textbf{AWS Solutions Architect -- Professional} certification
  * Dublin-based (Stamp 1G) — eligible for full-time employment in Ireland
  * End-to-end ownership narrative (design through delivery)
- Do NOT open with generic phrases like "Highly motivated", "Experienced professional", "Strong background in", or "Passionate about". Lead with the role title or the candidate's most relevant qualification for THIS specific role.
- Write 4-6 lines (60-100 words). NOT a one-liner. NOT a paragraph. A concise but substantive professional summary that gives a hiring manager enough to want to keep reading.
- Every word must be specific to this job. A reader should not be able to swap this summary onto a different resume for a different role.

KEYWORD INJECTION (adapted from career-ops methodology):
- Reformulate EXISTING bullets using JD vocabulary. Example: if the base says "built automated data pipelines" and the JD says "ETL orchestration", rewrite as "orchestrated ETL data pipelines". Same truth, JD words.
- Distribute keywords strategically:
  * Summary: must contain the top 5 JD keywords
  * First bullet of each job: must contain at least 1 JD keyword
  * Skills section: reorder to front-load JD-matching skills
- Prefer proof-point specifics over abstractions:
  * "Reduced MTTR by 35% across 8 production microservices" > "improved system reliability"
  * Use ONLY metrics that already exist in the base resume — do NOT invent numbers.
- PRESERVE all \textbf{} formatting from the base resume. Bold keywords and metrics must stay bold.

ARCHETYPE FRAMING:
{archetype_framing}

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


# ---------------------------------------------------------------------------
# Evidence pool (Task 17): ground tailoring in the candidate's own verified
# resume bullets (retrieval/bullets.py, Task 16). Bounding the model to facts
# that already appear in the user's own resume is a hallucination-mitigation
# control -- its effect on fabrication is measured in a follow-up task via
# the existing _check_fabrication guard. Flag-gated by BULLET_RAG (default
# off); see safe_evidence_block for why retrieval can never break tailoring.
# ---------------------------------------------------------------------------


def build_evidence_block(bullets: list[dict]) -> str:
    """Format retrieved bullets as a bounded evidence pool for the prompt."""
    if not bullets:
        return ""
    lines = "\n".join(f"- [{b['section']}] {b['text']}" for b in bullets)
    return (
        "\n\nEVIDENCE POOL — verified facts from this candidate's own resume:\n"
        f"{lines}\n"
        "Draw claims only from this pool. Do not invent experience, metrics, "
        "employers or technologies that do not appear above.\n"
    )


def safe_evidence_block(user_id: str, jd_text: str, k: int = 8) -> str:
    """Retrieval is an enhancement, never a dependency.

    If BULLET_RAG is off, or the vector store is unreachable, this returns ""
    and tailoring proceeds exactly as it did before this feature existed.
    """
    if os.environ.get("BULLET_RAG", "off") != "on":
        return ""
    try:
        return build_evidence_block(retrieve_evidence(user_id, jd_text, k=k))
    except Exception as exc:
        logger.warning(f"[tailor] evidence retrieval failed, continuing without: {exc}")
        return ""


def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]
    tailoring_depth = event.get("tailoring_depth")
    if tailoring_depth is None:
        tailoring_depth = "light" if event.get("light_touch") else "moderate"

    db = get_supabase()
    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET", "utkarsh-job-hunt")

    # Read job from jobs_raw
    job = db.table("jobs_raw").select("*").eq("job_hash", job_hash).execute()
    if not job.data:
        raise TailorError(f"Job {job_hash} not found in jobs_raw")
    job = job.data[0]

    # Get latest resume
    resume = db.table("user_resumes").select("*").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(1).execute()
    if not resume.data:
        raise TailorError(f"No base resume found for user {user_id}")
    base_tex = resume.data[0].get("tex_content", "")

    # Read user profile so the header-marker validation uses THIS user's
    # name + email, not hardcoded "Utkarsh Singh / 254utkarsh@gmail.com".
    # Multi-tenant fix: previously failed for any other user; now per-user.
    # Schema note: prod `users` table has `name` (and `first_name`/`last_name`)
    # but no `full_name` column; _derive_header_markers falls back to `name`.
    user_profile_resp = db.table("users").select("name, first_name, last_name, email") \
        .eq("id", user_id).limit(1).execute()
    user_profile = user_profile_resp.data[0] if user_profile_resp.data else None
    header_markers = _derive_header_markers(user_profile)

    base_preamble, base_body = _split_tex(base_tex)
    if not base_preamble:
        logger.error(f"[tailor] base resume missing \\begin{{document}} for user {user_id}")
        raise TailorError("base resume has no \\begin{document}")

    # Extract keywords and detect archetype
    from utils.keyword_extractor import extract_keywords
    description = job.get("description", "") or ""
    title = job.get("title", "")
    company = job.get("company", "")
    keywords = extract_keywords(description, max_keywords=15)
    archetype, archetype_framing = _detect_archetype(title, description)
    logger.info(f"[tailor] Archetype: {archetype} for '{title}' at '{company}'")

    keyword_section = ""
    if keywords:
        keyword_section = f"\n\nKEY JD REQUIREMENTS: {', '.join(keywords)}\nReformulate existing bullets using these terms. Do NOT fabricate experience."

    # Build prompts with archetype framing injected
    depth_note = {
        "light": _LIGHT_TOUCH_NOTE,
        "moderate": _MODERATE_NOTE,
        "heavy": _FULL_REWRITE_NOTE,
    }.get(tailoring_depth, _MODERATE_NOTE)
    system_prompt = f"{depth_note}\n\n{_SYSTEM_PROMPT.replace('{archetype_framing}', archetype_framing)}{keyword_section}"

    user_prompt = f"""Tailor this resume body for the following job.

Job: {title} at {company}
Description: {description[:4000]}

BASE RESUME BODY:
{base_body}

Return ONLY the tailored body. No \\documentclass, no \\newcommand, no \\begin{{document}}.

Reminder: your output MUST contain all six section headers verbatim: \\section*{{Summary}}, \\section*{{Technical Skills}}, \\section*{{Experience}}, \\section*{{Featured Projects}}, \\section*{{Education}}, \\section*{{Certifications}}.
PRESERVE all \\textbf{{}} formatting from the base resume."""

    # Evidence pool (Task 17): appends retrieved resume bullets, if any, so
    # the model is bounded to facts that actually appear in this candidate's
    # own resume. No-op (empty string) when BULLET_RAG is off or retrieval
    # fails -- see safe_evidence_block. Uses the full description, not the
    # [:4000]-truncated copy above, since this text is only embedded for
    # similarity search and never sent to the model itself. Must happen
    # before the council_complete call below, since that call reads
    # user_prompt by value.
    user_prompt += safe_evidence_block(user_id, description)

    # ALWAYS use council — no single-call bypass regardless of tier
    logger.info(f"[tailor] Council mode for {job_hash} (depth={tailoring_depth}, archetype={archetype})")
    try:
        response_dict = council_complete(
            prompt=user_prompt,
            system=system_prompt,
            task_description=(
                f"Tailor resume for '{title}' at '{company}' (archetype: {archetype}). "
                f"Depth: {tailoring_depth}. "
                "Pick the candidate with best JD keyword injection (reformulated, not fabricated), "
                "complete sections, active voice, preserved \\textbf formatting, "
                "proof-point specifics, and no filler phrases."
            ),
            n_generators=2,
            temperature=0.3,
        )
    except RuntimeError as e:
        logger.error(f"[tailor] Council failed: {e}")
        raise TailorError(f"council failed for {job_hash}: {e}") from e
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
        raise TailorError(f"AI returned empty body for {job_hash}")

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
    missing_header = _check_header_present(tailored_tex, header_markers)
    if missing_header:
        validation_errors.append(f"missing header markers: {missing_header}")

    if validation_errors:
        logger.warning(
            f"[tailor] validation failed for {job_hash}: {'; '.join(validation_errors)} "
            f"— falling back to base resume"
        )
        tailored_tex = base_tex
    else:
        # Quality validation — writing quality, not just structure
        quality_warnings = _check_banned_phrases(ai_body)
        quality_warnings.extend(_check_textbf_preservation(base_body, ai_body))
        base_skills_match = re.search(
            r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{",
            base_body, re.DOTALL,
        )
        if base_skills_match:
            quality_warnings.extend(_check_fabrication(base_skills_match.group(1), ai_body))

        if quality_warnings:
            logger.warning(f"[tailor] Quality warnings for {job_hash}: {'; '.join(quality_warnings[:5])}")
            # Retry once with explicit feedback
            retry_prompt = (
                user_prompt
                + "\n\nYour previous attempt had these quality issues:\n"
                + "\n".join(f"- FIX: {w}" for w in quality_warnings)
                + "\nPlease fix ALL of them. Return ONLY the corrected body."
            )
            try:
                retry_dict = ai_complete(retry_prompt, system=system_prompt, temperature=0.3)
                retry_body = retry_dict.get("content", "").strip()
                if "\\begin{document}" in retry_body:
                    _, retry_body = _split_tex(retry_body)
                retry_body = retry_body.removesuffix("\\end{document}").strip()
                # Re-check quality
                retry_quality = _check_banned_phrases(retry_body) + _check_textbf_preservation(base_body, retry_body)
                if len(retry_quality) < len(quality_warnings):
                    logger.info(f"[tailor] Retry improved quality: {len(quality_warnings)} -> {len(retry_quality)} warnings")
                    ai_body = retry_body
                    response_dict = retry_dict
                    tailored_tex = _splice_tex(base_preamble, ai_body)
                    # Re-escape body
                    body_start = tailored_tex.find(r"\begin{document}")
                    if body_start > 0:
                        preamble_part = tailored_tex[:body_start]
                        body_part = tailored_tex[body_start:]
                        body_part = re.sub(r'(?<!\\)#', r'\\#', body_part)
                        body_part = re.sub(r'(?<!\\)&(?!\\)', r'\\&', body_part)
                        tailored_tex = preamble_part + body_part
                else:
                    logger.info("[tailor] Retry did not improve quality, keeping original")
            except RuntimeError:
                logger.warning("[tailor] Quality retry failed, keeping original")

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
