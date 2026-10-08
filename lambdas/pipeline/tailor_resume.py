import logging
import os
import re

import boto3

from ai_helper import ai_complete, council_complete, get_supabase, rewrite_budget

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
from guardrails.output_guards import check_weak_bullet_openers as _check_weak_openers
from guardrails.output_guards import check_brace_balance as _check_brace_balance
from guardrails.output_guards import check_required_sections as _check_required_sections
from guardrails.output_guards import check_fabrication as _check_fabrication
from guardrails.output_guards import check_unquantified_bullets as _check_unquantified
from guardrails.output_guards import check_header_present as _check_header_present
from guardrails.output_guards import check_near_empty as _check_near_empty
from guardrails.output_guards import check_prompt_echo as _check_prompt_echo
from guardrails.output_guards import check_textbf_preservation as _check_textbf_preservation

# The user's resume-composition rules (how many experience entries, how many
# projects, how many pages) and the counting that enforces them. `shared` is a
# repo-root package that reaches zip Lambdas via /opt/python (layer/build.sh
# FIRST_PARTY) and the container image via Dockerfile.lambda's `COPY shared/`,
# so the qualified `shared.*` spelling resolves in every deploy path — same as
# score_batch.py's `from shared.work_auth import ...`. `check_output` is
# aliased because guardrails/output_guards.py exports a different function of
# the same name.
from shared.composition_policy import check_output as _check_composition
from shared.composition_policy import check_preferences as _check_preferences
from shared.composition_policy import (
    compose_from_corpus,
    describe_violations,
    render_for_prompt,
)

# Every column the tailor needs from the user's row. composition_policy was
# added by supabase/migrations/20260929200000_users_composition_policy.sql.
_USER_COLUMNS = "name, first_name, last_name, email, composition_policy"
_USER_COLUMNS_PRE_MIGRATION = "name, first_name, last_name, email"
_COMPOSITION_MIGRATION = "supabase/migrations/20260929200000_users_composition_policy.sql"


class TailorError(Exception):
    """Raised when tailoring cannot produce a resume for this job.

    MUST be raised, never returned. Step Functions treats a returned dict as a
    SUCCESSFUL invocation, so returning {"error": ...} bypasses the Catch on the
    TailorResume state; the failure then surfaces in CompileResume as an
    unhandled JSONPath error on $.tailor_result.tex_s3_key, which fails the
    whole execution instead of just this one job in the Map.

    Raising lets the existing Catch route to SaveJobAfterError as designed.
    """


class _RetryRejected(Exception):
    """Internal: the quality retry produced something unusable.

    Module-private and never escapes handler() — it only unwinds out of the
    retry block back to "keep the council's body", which is the same outcome
    as a retry that simply didn't improve anything. Deliberately NOT a
    TailorError: nothing about the job has failed.
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


def _fetch_user_profile(db, user_id: str) -> dict | None:
    """The user's row, tolerating a database that predates composition_policy.

    PostgREST fails the WHOLE request when a selected column is unknown to it,
    and this select gates every tailor — it supplies the header markers the
    hard gate below validates against. So deploying this code ahead of the
    migration would not degrade one feature to its defaults; it would take
    tailoring down for every job, in every user's run.

    Retry without the column instead, and name the migration in the log so the
    fix is one line away rather than a debugging session. NULL and "column
    absent" mean the same thing here: use the defaults.
    """
    try:
        resp = db.table("users").select(_USER_COLUMNS).eq("id", user_id).limit(1).execute()
    except Exception as exc:
        logger.warning(
            "[tailor] could not read users.composition_policy (%s) — using the "
            "default composition rules. Apply %s.", exc, _COMPOSITION_MIGRATION,
        )
        resp = db.table("users").select(_USER_COLUMNS_PRE_MIGRATION) \
            .eq("id", user_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def _surviving_blocking(guard_report) -> list[str] | None:
    """Block-severity violations that outlived the council's repair budget.

    `None` and `[]` are different facts and the caller stores both:

        None  the engine reported no guard verdict at all. The legacy engine
              has no guard nodes, so it cannot have measured anything.
        []    a guard ran and nothing blocking survived.

    Collapsing the first into the second claims a clean verdict from an engine
    that never looked -- the same lie as a status that cannot fail, and the
    reason `shared.resume_verdict` keeps the distinction too.

    Falls back to `violations` when `blocking` is absent: a guard_report
    written before severity survived `to_dict` has no `blocking` key, and
    reporting nothing for those would make this change look as though it had
    cleaned them up.
    """
    if guard_report is None:
        return None
    if "blocking" in guard_report:
        return list(guard_report.get("blocking") or [])
    return list(guard_report.get("violations") or [])


def _quality_warnings(body: str, base_body: str, fabrication_baseline) -> list[str]:
    """Every writing-quality check, in one place.

    ONE function for both sides of the retry comparison, structurally. The two
    lists were built separately and drifted: the retry's rubric omitted
    fabrication, so a retry that kept every fabricated skill scored as an
    improvement whenever it dropped one banned phrase, and the fabrication
    shipped (CLAUDE.md rule 14). Adding a check to one side and forgetting the
    other is now impossible rather than merely discouraged -- which is the only
    version of that rule that survives the next person adding a check.
    """
    warnings = _check_banned_phrases(body)
    warnings.extend(_check_weak_openers(body))
    # Yale's third term — the quantified result — is the one that gets dropped.
    # Advisory by measurement, not by preference: 0 of 100 live résumés have
    # every bullet quantified, so blocking would reject all of them, and a hard
    # counter is satisfied by inventing a figure. See check_unquantified_bullets.
    warnings.extend(_check_unquantified(body))
    warnings.extend(_check_textbf_preservation(base_body, body))
    if fabrication_baseline:
        warnings.extend(_check_fabrication(fabrication_baseline, body))
    return warnings


def _enforce_composition(
    *,
    ai_body: str,
    policy: dict | None,
    user_prompt: str,
    system_prompt: str,
    max_tokens: int,
    job_hash: str,
) -> str:
    r"""Count the composition rules in the generated body; repair once if broken.

    This is the half that was missing. `_validate_macro_arities` checks that
    each `\jobentry` call has four arguments; nothing counted how many
    `\jobentry` calls the model emitted, so "EXACTLY 3 PROJECTS" was a request
    the model could decline in silence.

    Returns the body to ship: the repaired one if the repair landed, otherwise
    the original. A repair is accepted ONLY when it is structurally intact AND
    fully compliant. Swapping in a body that still breaks the rules would cost
    the quality-retry work already done on the original for no compliance gain,
    and a body that complied by being truncated is not a repair at all — the
    same "structure before style" rule the quality retry applies.

    The caller re-counts whatever ends up being shipped, so the returned body
    is never assumed compliant.
    """
    violations = _check_composition(ai_body, policy)
    if not violations:
        return ai_body

    logger.warning(
        "[tailor] composition rules broken for %s (%s) — repairing once",
        job_hash, "; ".join(violations),
    )
    repair_prompt = f"{user_prompt}\n\n{describe_violations(violations)}"
    try:
        repair = ai_complete(
            repair_prompt, system=system_prompt, temperature=0.3, max_tokens=max_tokens,
        )
    except RuntimeError as exc:
        logger.error(
            "[tailor] composition repair call failed for %s (%s); shipping a "
            "document that breaks: %s", job_hash, exc, "; ".join(violations),
        )
        return ai_body

    repaired = (repair.get("content") or "").strip()
    if "\\begin{document}" in repaired:
        _ignored, repaired = _split_tex(repaired)
    repaired = repaired.removesuffix("\\end{document}").strip()

    rejected = ""
    if repair.get("truncated"):
        rejected = "cut off at max_tokens"
    elif not repaired:
        rejected = "empty body"
    elif not _check_brace_balance(repaired):
        rejected = "brace imbalance"
    elif arity_issues := _validate_macro_arities(repaired):
        rejected = f"arity: {'; '.join(arity_issues[:3])}"
    elif missing := _check_required_sections(repaired):
        rejected = f"missing sections {missing}"
    elif remaining := _check_composition(repaired, policy):
        rejected = f"still breaks {'; '.join(remaining)}"

    if rejected:
        logger.error(
            "[tailor] composition repair did not land for %s (%s); shipping a "
            "document that breaks: %s", job_hash, rejected, "; ".join(violations),
        )
        return ai_body

    logger.info(
        "[tailor] composition repair fixed %s (was: %s)", job_hash, "; ".join(violations),
    )
    return repaired


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

CUSTOM MACROS (already defined — use with EXACT argument counts). The examples
show the ARGUMENT SHAPE only; take every real value from the base resume body
you are given, never from these placeholders:
- \jobentry{company}{location}{dates}{title}       — 4 args
  Example: \jobentry{Example Corp}{Dublin, Ireland (Remote)}{Jun 2022 -- Jul 2024}{\textbf{\textit{Software Engineer}}}
- \projectentry{name}{dates}{tech}                 — 3 args (no URL)
  Example: \projectentry{Example Project}{Apr 2025 -- Jul 2025}{Node.js, React, FastAPI, MySQL, Docker, AWS}
- \projectentryurl{name}{dates}{url}{url-text}{tech} — 5 args (with clickable URL)
  Example: \projectentryurl{Example Project}{Jan 2026 -- Present}{https://example.com/project}{example.com/project}{React Native, TypeScript, Firebase}

Each macro call MUST be followed by a \begin{itemize}...\end{itemize} block with \item bullets. Do NOT put \begin{itemize} inside the macro call.

RULES:
1. NEVER fabricate experience, skills, or accomplishments. Only reword, reorder, and emphasize what already exists.
2. Make targeted, surgical edits. Do NOT rewrite the entire resume.
3. Focus changes on:
   - Summary: adjust emphasis for this role
   - Skills: reorder CATEGORIES to put the most relevant first. PRESERVE ALL 8 CATEGORIES from the base resume — do NOT merge or drop any. Keep the parenthetical details (e.g., "AWS (EC2, ECS/Fargate, EKS, Lambda, RDS, S3, API Gateway, SQS/SNS, CloudFront, Route 53)"). You may reorder items within a category to front-load JD-relevant technologies
   - Experience bullets: reorder within each job; tweak wording to match the job listing's terminology
   - Projects: SELECT from the candidate's projects, within the limit given
     in the COMPOSITION RULES below. Pick the ones most relevant to this job.
     * COMPLETELY DELETE every project you do not select. Remove its
       \projectentry/\projectentryurl AND its \begin{itemize}...\end{itemize}
       block entirely. Do NOT leave empty project shells.
     * Rewrite the selected projects' bullets to emphasize the aspects that
       match the JD. Their names, dates and tech-stack headers stay intact.
4. The resume must remain truthful.
5. PAGE LAYOUT (CRITICAL):
   - The page count is given in the COMPOSITION RULES below.
   - Page 1 carries the header, Summary, Technical Skills and the selected
     experience entries. Page 2 carries the selected projects, Education and
     Certifications.
   - NEVER emit \clearpage, \newpage, \pagebreak or \cleardoublepage. LaTeX
     breaks the page where it needs to; a forced break wastes the rest of the
     page it fires on and cannot be recovered by trimming content.
     (This bullet previously said to KEEP a \clearpage, "the template uses it
     to land that section on page 2". True of the BUNDLED template --
     resumes/fullstack.tex:84 carries one deliberately, to fix an awkward
     header-on-page-1 split -- and not true of what production actually
     tailors, since neither user_resumes row contains one. The model emitted
     one regardless, in 87 of 272 live resumes. The forcing works only while
     the content before it happens to fill one page: of the 15 resumes that
     overran the two-page budget, 14 carried a forced break, and one compiled
     to per-page text lengths of [4010, 87, 3654] after every reduction lever
     had been pulled -- page 2 holding 87 characters. Removing that one macro:
     [4010, 3741]. shared.fit_to_pages.strip_forced_breaks now does it before
     every compile, whatever the document's origin, because CLAUDE.md #4 --
     this instruction is a request, that strip is the guarantee. Still worth
     asking: each such document burned 9 pointless tectonic compiles.)
   - If content overflows, CUT bullet points from the least relevant entries
     rather than dropping a whole section.
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


def escape_body_specials(tex: str) -> str:
    r"""Escape LaTeX specials in the document BODY, never the preamble.

    The preamble uses #1, #2 as macro parameters and `{%` as a line
    continuation; escaping either breaks every macro in the file. That is not
    hypothetical — escaping # across the whole document is what broke all of
    them on 2026-04-09.

    `%` is the one that matters most and was missing until 2026-09-29. It does
    not fail where it occurs: it comments out the REST OF THE LINE, so the
    brace closing an enclosing \textbf{...} is eaten and LaTeX runs on into
    whatever follows. Production that morning:

        ! File ended while scanning use of \textbf .
        Runaway argument?
        {MTTR by 35\section *{Technical Skills} ...

    Two of ten resumes were lost to a single "35%". Brace counting cannot catch
    it — the } is in the file; LaTeX simply never sees it. # and & fail loudly
    at the offending token, which is why they were noticed years earlier.

    One function because the escaping had been copy-pasted at two call sites
    (the initial splice and the quality-retry re-splice) and both were missing
    the same character.
    """
    body_start = tex.find(r"\begin{document}")
    if body_start <= 0:
        return tex
    preamble, body = tex[:body_start], tex[body_start:]
    body = re.sub(r"(?<!\\)#", r"\\#", body)            # C#, F#
    body = re.sub(r"(?<!\\)&(?!\\)", r"\\&", body)      # R&D, AT&T
    body = re.sub(r"(?<!\\)%", r"\\%", body)            # 35% -> 35\%
    return preamble + body


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
    # Newest TAILORABLE resume, not simply newest. /api/resumes/upload stores
    # PDF-extracted plain text in tex_content ("Store raw text for now"), and
    # selection was newest-wins with no validity check — so on 2026-09-28 a PDF
    # upload silently replaced a working LaTeX base resume and every tailoring
    # attempt failed with "base resume has no \begin{document}", surfacing in
    # the UI as "Regenerate failed: Pipeline failed".
    from shared.resume_format import describe_why_not_latex, fetch_tailorable_resume

    base = fetch_tailorable_resume(db, user_id)
    if base.skipped:
        logger.warning(
            "[tailor] skipped %d newer resume(s) that are not LaTeX — %s",
            base.skipped, describe_why_not_latex(base.newest_tex),
        )
    if base.row is None:
        raise TailorError("No tailorable base resume: " + base.why_unusable(user_id))
    base_tex = base.tex

    # Read user profile so the header-marker validation uses THIS user's
    # name + email, not hardcoded "Utkarsh Singh / 254utkarsh@gmail.com".
    # Multi-tenant fix: previously failed for any other user; now per-user.
    # Schema note: prod `users` table has `name` (and `first_name`/`last_name`)
    # but no `full_name` column; _derive_header_markers falls back to `name`.
    #
    # The same row carries composition_policy: the user's rules for composing
    # a resume FROM their corpus (entry caps, page count). NULL means the
    # defaults in shared/composition_policy.py, so an un-configured user keeps
    # today's behaviour.
    user_profile = _fetch_user_profile(db, user_id)
    header_markers = _derive_header_markers(user_profile)
    composition_policy = (user_profile or {}).get("composition_policy")

    base_preamble, base_body = _split_tex(base_tex)
    if not base_preamble:
        logger.error(f"[tailor] base resume missing \\begin{{document}} for user {user_id}")
        raise TailorError("base resume has no \\begin{document}")

    # Base Skills-section text: seeds BOTH the council's guard_output_node
    # fabrication check (task="tailor" below -- see the council_complete call)
    # and the post-hoc quality_warnings check further down. Computed once
    # here so both call sites see the same value instead of the graph-level
    # guard silently evaluating fabrication against an empty baseline (which
    # would make it pass everything regardless of what the model wrote).
    base_skills_match = re.search(
        r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{",
        base_body, re.DOTALL,
    )
    base_skills_text = base_skills_match.group(1) if base_skills_match else ""

    # The fabrication check compares against EVERY stored resume row, not just
    # the one being tailored. "Has the candidate ever claimed this skill?" is a
    # question about the whole profile; asking it of one revision convicts them
    # of content an earlier row had and this one dropped. Measured: with
    # word-boundary matching, the production row alone flags 97 of 140 real
    # resumes and the union flags 23 -- the 74 differences are all Java, which
    # the April row lists and the September row does not. Falls back to the
    # Skills section when no union is available, which keeps the guard armed
    # (check_output gates fabrication on this string being non-empty).
    fabrication_baseline = base.all_tex or base_skills_text

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
    # The candidate's own composition rules, rendered from their profile
    # rather than compiled into the prompt. Everything countable in here is
    # ALSO counted after generation — see _enforce_composition below. A rule
    # only stated in a prompt is a request.
    policy_block = render_for_prompt(composition_policy)
    system_prompt = (
        f"{depth_note}\n\n"
        f"{_SYSTEM_PROMPT.replace('{archetype_framing}', archetype_framing)}"
        f"{keyword_section}\n\n{policy_block}"
    )

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

    # The answer to this prompt is a re-emission of base_body, so the output
    # budget has to be sized from base_body -- not left at the council's 4096
    # default, which is smaller than some base resumes need and leaves a
    # reasoning model nothing to write with once it has finished thinking.
    # That shortfall is what produced tailored bodies ending inside Technical
    # Skills; see ai_helper.rewrite_budget.
    tailor_max_tokens = rewrite_budget(base_body)

    # ALWAYS use council — no single-call bypass regardless of tier
    logger.info(
        f"[tailor] Council mode for {job_hash} (depth={tailoring_depth}, "
        f"archetype={archetype}, max_tokens={tailor_max_tokens})"
    )
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
            max_tokens=tailor_max_tokens,
            task="tailor",
            base_skills=fabrication_baseline,
            base_body=base_body,
            # header_markers is deliberately NOT forwarded here. The graph's
            # guard_output_node evaluates `winner.content`, which is the
            # model's raw BODY-only output -- the prompt above explicitly
            # forbids \documentclass/\begin{document}. The header markers
            # (the user's name/email, from _derive_header_markers above) live
            # only in `base_preamble`, spliced in AFTER council_complete
            # returns (see "Splice:" below). Passing header_markers through
            # would arm check_header_present against content structurally
            # incapable of containing them -- a block-severity violation on
            # every single call, burning the full 2-round repair budget for a
            # check that can never pass at this stage, for zero benefit (a
            # repair prompt telling the model to add its own name/email into
            # the resume BODY would actively corrupt the output). The
            # post-splice `_check_header_present(tailored_tex, header_markers)`
            # hard gate below is the correct place this is already enforced,
            # against the actually-spliced text.
        )
    except RuntimeError as e:
        logger.error(f"[tailor] Council failed: {e}")
        raise TailorError(f"council failed for {job_hash}: {e}") from e
    ai_response = response_dict["content"]
    # Block-severity violations that SURVIVED the council's bounded repair
    # loop, i.e. what `quality_gate` finalized best-effort with. `None` when
    # the engine reports no guard verdict at all, which is a different fact
    # from "it reported a clean one" and must not be flattened into [].
    surviving_guard_violations = _surviving_blocking(response_dict.get("guard_report"))
    if surviving_guard_violations:
        logger.warning(
            "[tailor] %s ships with %d unresolved block-severity violation(s): %s",
            job_hash, len(surviving_guard_violations),
            "; ".join(surviving_guard_violations[:4]),
        )
    if response_dict.get("truncated"):
        # Say it at the point of failure. The hard gates below will reject
        # this body for missing sections and fall back to the base resume,
        # and without this line that log reads as "the model ignored the
        # instructions" when what actually happened is that it never got to
        # finish. Not raised: the fallback is still the right outcome.
        logger.warning(
            f"[tailor] council winner for {job_hash} was cut off at "
            f"max_tokens={tailor_max_tokens} ({len(ai_response)} chars) — the "
            "tailored body is incomplete and will not pass the section gates"
        )

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
    # (`re` is the module imported at the top of this file -- a redundant
    # local `import re` used to live on this line. Harmless on its own, but
    # it makes `re` a local name for the ENTIRE function body per Python's
    # scoping rules, which broke as soon as this function gained an earlier
    # module-level `re.search` call above -- see the base_skills_text block
    # near the top of this function. Removed rather than worked around.)
    tailored_tex = escape_body_specials(tailored_tex)

    # REMOVED here: a `word_count < 500` fallback that sat on this line and
    # swapped `tailored_tex = base_tex` WITHOUT appending to the
    # `validation_errors` list built below. The gates below then ran against
    # the corpus, which passes all of them, so `used_fallback` came back False
    # for a run that fell back, the summary line read "(ok)", and the quality
    # retry went on to score a body that had already been thrown away. That is
    # CLAUDE.md rule 2 — a status that cannot distinguish "did the work" from
    # "did nothing" — and it also fed `scripts/bench_retrieval.py`, which
    # reports fallback rates per arm off this same field.
    #
    # It was deleted rather than wired into `validation_errors`, because
    # reporting its verdict honestly would have meant standing behind a verdict
    # that is wrong three ways:
    #
    # 1. THE INSTRUMENT UNDERCOUNTS. `\\[a-zA-Z]+\*?(\{[^}]*\})*` deletes a
    #    macro AND its braced arguments, so `\textbf{Python}` scores zero
    #    words. Measured on this repo's real resumes, counted words over true
    #    words: fullstack.tex 861/1097 (0.785), sre_devops.tex 846/1033
    #    (0.819), tests/unit/realistic_resume_body 150/201 (0.746). A floor
    #    advertised as 500 words was enforcing 611-670 on real documents, and
    #    it bit hardest on exactly the LaTeX-dense bodies that carry the most
    #    content.
    # 2. IT FIRED ON REAL RESUMES. Over the 740 stored tailored outputs
    #    (PR #170's population, `exclude_untailored=True`): 52 hits, 7.03%,
    #    anchor counts 131-228 against a corpus range of 102-289. Those are
    #    resumes, not stubs. `check_near_empty` at floor 40 fires on 1 of the
    #    same 740 — the one genuinely empty document.
    # 3. ITS STATED PURPOSE IS MEASURED PROPERLY ELSEWHERE, AND ITS REMEDY
    #    WORKED AGAINST IT. The comment called it a "page length check", but
    #    page rules can only be answered by a compiled PDF, and since
    #    shared/page_check.py they are — per-page text floor included, wired
    #    into compile_latex.py downstream of this function. Meanwhile what
    #    this branch did about a suspected-too-short resume was ship
    #    `base_tex`: the master corpus, which is longer, not shorter. Both of
    #    this repo's real corpus resumes measure THREE pages in
    #    tests/unit/test_page_check.py against a policy of two, and the
    #    composition check further down already logs that a base-resume
    #    fallback breaks the caps "by definition". Swapping a short document
    #    for an over-long one is not a page-length remedy.
    #
    # Nothing is left uncovered. The one production document this branch
    # would have caught alone is b45671b7ec5c, whose body is the word "and":
    # `check_required_sections` already blocks it, and `check_near_empty`
    # below blocks it on the instrument built for the question.

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

    # Two 2026-09-30 defects, both found in SHIPPED resumes, both terminal here
    # rather than only in the council's guard. `check_output` blocks on both, so
    # the council gets two bounded repair attempts first — but `quality_gate`
    # routes to `finalize` once `repair_attempts >= 2`, best-effort, with the
    # rejected body intact. A route that ends in the same place whether the
    # repair worked or was abandoned is not a gate (CLAUDE.md rule 2). This is
    # the gate: a body that still fails here never reaches S3, because
    # `validation_errors` forces the corpus instead and `used_fallback: True`
    # says so in the return value.
    #
    # Measured on `ai_body`, not `tailored_tex`, and the difference is
    # load-bearing for both. The spliced document carries the base preamble,
    # which is ~1.2KB of \usepackage and \newcommand identical in every output:
    # it contributes 9 anchors to b45671b7ec5c, whose body is the single word
    # "and", and it is the one stretch of text a prompt-echo marker firing on it
    # would flag in 100% of documents. The body is also exactly what
    # `check_output` sees, so the two gates judge the same string.
    echoed = _check_prompt_echo(ai_body)
    if echoed:
        validation_errors.append(f"prompt echo: {'; '.join(echoed[:3])}")
    near_empty = _check_near_empty(ai_body)
    if near_empty:
        validation_errors.append(f"near-empty output: {near_empty[0]}")

    # The writing warnings belonging to the body that actually SHIPS, which is
    # not always the body that was first measured. Two ways it diverges:
    #   * the fallback below ships `base_tex`, which `_quality_warnings` never
    #     saw -- so the honest value is None (never measured), not []
    #   * an accepted retry ships `retry_body`, whose warnings are
    #     `retry_quality`; reporting `quality_warnings` there would describe
    #     the document we threw away
    # Same asymmetry as the retry comparison a few lines down (CLAUDE.md #14):
    # a number is only worth storing if it describes the artefact the user gets.
    shipped_quality: list[str] | None = None

    if validation_errors:
        logger.warning(
            f"[tailor] validation failed for {job_hash}: {'; '.join(validation_errors)} "
            f"— falling back to base resume"
        )
        tailored_tex = base_tex
    else:
        # Quality validation — writing quality, not just structure
        quality_warnings = _quality_warnings(ai_body, base_body, fabrication_baseline)
        shipped_quality = quality_warnings

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
                # Same budget as the council call above: the retry asks for the
                # whole body again, so the 4096 default would truncate it for
                # exactly the reason the council call no longer does.
                retry_dict = ai_complete(
                    retry_prompt, system=system_prompt, temperature=0.3,
                    max_tokens=tailor_max_tokens,
                )
                retry_body = retry_dict.get("content", "").strip()
                if "\\begin{document}" in retry_body:
                    _, retry_body = _split_tex(retry_body)
                retry_body = retry_body.removesuffix("\\end{document}").strip()
                # Structure before style. "Fewer warnings" is not an
                # improvement if the retry got there by dropping half the
                # document: a shorter body trivially contains fewer banned
                # phrases, so an incomplete retry scores BETTER on the count
                # below than the complete body it would replace. The hard
                # gates above already ran on `ai_body`; nothing re-ran them on
                # `retry_body`, so this is where a truncated retry used to be
                # able to overwrite a valid tailored resume.
                retry_missing = _check_required_sections(retry_body)
                if retry_dict.get("truncated") or retry_missing:
                    logger.warning(
                        f"[tailor] Discarding quality retry for {job_hash}: "
                        + ("cut off at max_tokens" if retry_dict.get("truncated")
                           else f"missing sections {retry_missing}")
                    )
                    raise _RetryRejected
                # Re-check quality. The two sides of this comparison MUST count
                # the same checks. They did not: `quality_warnings` above
                # includes _check_fabrication, and this line omitted it, so the
                # retry was scored on a strictly easier rubric than the body it
                # was replacing. A retry that kept every fabricated skill still
                # counted as "improved" whenever it dropped one banned phrase --
                # 1 fabrication + 2 phrases (3) beaten by the same fabrication +
                # 2 phrases (2) -- and the fabrication shipped. Same asymmetry
                # the comment above guards against for truncation, one check
                # further along. Since 55ebff2 a fabrication is a BLOCKING
                # violation in the council's own guard, so leaving it out of the
                # retry's rubric let this path accept what that guard exists to
                # reject.
                retry_quality = _quality_warnings(
                    retry_body, base_body, fabrication_baseline)
                if len(retry_quality) < len(quality_warnings):
                    logger.info(f"[tailor] Retry improved quality: {len(quality_warnings)} -> {len(retry_quality)} warnings")
                    ai_body = retry_body
                    shipped_quality = retry_quality
                    response_dict = retry_dict
                    tailored_tex = _splice_tex(base_preamble, ai_body)
                    tailored_tex = escape_body_specials(tailored_tex)
                else:
                    logger.info("[tailor] Retry did not improve quality, keeping original")
            except _RetryRejected:
                pass  # already logged above; keep the council's body
            except RuntimeError:
                logger.warning("[tailor] Quality retry failed, keeping original")

    # Composition enforcement — the rules above are COUNTED, not just asked
    # for. Runs last so it has the final word on whichever body the quality
    # retry settled on, and only when a generated document is what ships:
    # `tailored_tex is base_tex` means an earlier fallback (short body, or a
    # failed hard gate) already chose the corpus, and re-splicing a repaired
    # body over that deliberate fallback would undo it.
    if tailored_tex is not base_tex:
        repaired_body = _enforce_composition(
            ai_body=ai_body,
            policy=composition_policy,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=tailor_max_tokens,
            job_hash=job_hash,
        )
        if repaired_body != ai_body:
            ai_body = repaired_body
            tailored_tex = escape_body_specials(_splice_tex(base_preamble, ai_body))

    # Count the document that is ACTUALLY being shipped, whichever branch
    # produced it. An empty list on the return value is therefore a claim that
    # the .tex written below was measured and complies — not that a repair was
    # attempted. A base-resume fallback normally lands here non-empty, because
    # the base resume is the corpus and a corpus exceeds the caps by
    # definition; that is worth reporting rather than papering over.
    composition_violations = _check_composition(tailored_tex, composition_policy)
    if composition_violations:
        # Last resort, and deterministic on purpose. Until 2026-09-30 this
        # branch only LOGGED, with a comment arguing that a corpus exceeds the
        # caps "by definition" and that saying so beat papering over it. That
        # reasoning was wrong in the way CLAUDE.md rule 13 describes: the check
        # fired, correctly, on every fallback, and nothing acted on it, so the
        # user's 3-entry policy produced 5-entry resumes whenever a hard gate
        # tripped. Measured on the live corpus: 5 experience entries and 5
        # projects, shipped, with the violation in CloudWatch.
        #
        # Trimming needs no model. The entries are written and ordered already;
        # which to keep is arithmetic over the user's own `prefer` rules plus
        # the caps. So it runs on whatever is about to ship — a base-resume
        # fallback or a generated body whose two AI repair rounds did not
        # converge — and the result is accepted only if it FULLY complies.
        # A partial trim would mean mangling the document for nothing.
        # WHICH document to compose from is the decision here. A generated
        # body that broke a PREFERENCE picked the wrong entries, not merely too
        # many, and composing can only delete: measured on 385ba44d33e6, the
        # council emitted 3 experience entries -- within the cap, so nothing
        # fired -- having chosen Seattle Kraken and omitted UT Arlington IT.
        # Trimming that body would drop Kraken and leave two entries, still
        # without the role the user asked for. The corpus holds all three, so a
        # preference break composes from the corpus instead.
        #
        # Cap-only violations keep the generated body: its tailoring is real
        # work and the only thing wrong with it is length.
        pref_broken = _check_preferences(tailored_tex, composition_policy)
        seed = base_tex if (tailored_tex is not base_tex and pref_broken) else tailored_tex
        if seed is not tailored_tex:
            logger.warning(
                "[tailor] %s broke a composition PREFERENCE (%s); composing "
                "from the corpus instead of trimming the generated body",
                job_hash, "; ".join(pref_broken),
            )
        trimmed, actions = compose_from_corpus(seed, composition_policy)
        still_wrong = _check_composition(trimmed, composition_policy)
        if actions and not still_wrong:
            source = "base-resume fallback" if tailored_tex is base_tex else "generated body"
            logger.info(
                "[tailor] composed %s for %s down to the policy: %s",
                source, job_hash, "; ".join(actions),
            )
            tailored_tex = trimmed
            composition_violations = still_wrong
        else:
            logger.warning(
                "[tailor] %s for %s breaks the composition rules and could NOT "
                "be trimmed (%s); shipping as-is: %s",
                "base-resume fallback" if tailored_tex is base_tex else "generated body",
                job_hash, "; ".join(still_wrong) or "no entries located",
                "; ".join(composition_violations),
            )

    # Write to S3
    tex_key = f"users/{user_id}/resumes/{job_hash}_tailored.tex"
    s3.put_object(Bucket=bucket, Key=tex_key, Body=tailored_tex.encode("utf-8"))

    # Update job record. critique_outcome records WHETHER the council
    # adjudicated this result or fell back to the first candidate — measured at
    # 26% adjudication over 80 rounds, and previously discoverable only by
    # grepping CloudWatch. Recording it makes the rate one SQL query.
    outcome = response_dict.get("critique_outcome", "unknown")
    update = {
        "resume_version": 1,
        "tailoring_model": f"{response_dict.get('provider', 'council')}:{response_dict.get('model', 'consensus')}",
        "critique_outcome": outcome,
    }
    try:
        db.table("jobs").update(update).eq("user_id", user_id) \
            .eq("job_hash", job_hash).execute()
    except Exception as exc:
        # PostgREST fails the WHOLE update on an unknown column, with PGRST204
        # and the wording "schema cache" — never "does not exist", which is raw
        # Postgres phrasing and the reason an earlier guard in this repo never
        # once fired. Losing the outcome is acceptable; losing the
        # tailoring_model write is not.
        if "PGRST204" not in str(exc) and "schema cache" not in str(exc).lower():
            raise
        logger.warning(
            "jobs.critique_outcome is missing — recording without it. Apply "
            "supabase/migrations/20260930020000_jobs_critique_outcome.sql"
        )
        update.pop("critique_outcome")
        db.table("jobs").update(update).eq("user_id", user_id) \
            .eq("job_hash", job_hash).execute()

    if outcome != "adjudicated":
        logger.warning("[tailor] %s was NOT adjudicated (outcome=%s) — the "
                       "winner is candidate 1, unreviewed", job_hash, outcome)

    # Both this line and `used_fallback` below read `validation_errors`, and
    # they must keep reading it: the defect this replaced was a fallback that
    # reached neither. The reason is repeated here rather than left to the
    # warning logged at the point of failure so that one line carries the
    # whole verdict — grepping for "[tailor] Moderate tailor" now tells you
    # WHICH gate fired, not merely that something did.
    logger.info(
        f"[tailor] {tailoring_depth.capitalize()} tailor for {job_hash} "
        + (f"(fallback: {'; '.join(validation_errors)})" if validation_errors
           else "(ok)")
    )
    return {
        "job_hash": job_hash,
        "tex_s3_key": tex_key,
        "user_id": user_id,
        "used_fallback": bool(validation_errors),
        # Empty means the shipped .tex was counted against this user's
        # composition rules and complies. Non-empty means it does not, and the
        # repair above did not fix it — surfaced rather than swallowed.
        "composition_violations": composition_violations,
        # Writing quality of the SHIPPED body. Until 2026-10-07 this was
        # computed, logged at WARNING, used to choose between two attempts and
        # then dropped before the return, so no column, dashboard or alarm
        # could see it. `None` means it was never measured (the fallback path)
        # and is graded as such by shared.resume_verdict -- an empty list is
        # the stronger claim that the shipped body was checked and is clean.
        "quality_warnings": shipped_quality,
        # What the council's guard still objected to when it gave up. Recorded
        # rather than graded, for now: `shared.resume_verdict` does not take it
        # as a grading input because its false-positive rate at this position
        # has never been measured, and CLAUDE.md #16 is explicit that a
        # detector's FP rate is measured BEFORE it blocks, not after. Storing
        # it is what makes that measurement possible -- the 41% figure above
        # was only obtainable from CloudWatch, for one batch, by hand.
        "guard_violations": surviving_guard_violations,
    }
