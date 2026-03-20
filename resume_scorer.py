"""Three-perspective resume scoring engine.

Before tailoring a resume, scores it from 3 viewpoints:
1. ATS (Applicant Tracking System) — keyword matching, formatting
2. Hiring Manager — relevance, impact, culture fit
3. Technical Recruiter — technical depth, skills alignment

All 3 scores must be 85+ (out of 100) before the resume is considered
ready. If any score is below 85, the AI iteratively improves the resume
(up to 3 rounds) until all scores pass or we give up.
"""

from __future__ import annotations
import json
import logging
from typing import Dict
from scrapers.base import Job
from ai_client import AIClient
from matcher import extract_json

logger = logging.getLogger(__name__)


SCORER_SYSTEM_PROMPT = r"""You are an expert at evaluating resumes from three distinct perspectives. You must score a TAILORED resume against a specific job listing.

Score from exactly these 3 viewpoints (0-100 each):

1. **ATS Score** — Applicant Tracking System perspective:
   - Keyword match: Does the resume contain the key terms from the job description?
   - Formatting: Is the resume well-structured and readable (no tables/images that break ATS)?
   - Section structure: Are standard sections present (Experience, Education, Skills)?
   - Job title alignment: Does the resume's implied role match the listing?

2. **Hiring Manager Score** — The person who'd manage this hire:
   - Relevant impact: Do the bullet points show measurable results relevant to THIS role?
   - Experience narrative: Does the career story make sense for this position?
   - Culture/team fit signals: Does the candidate seem like they'd thrive here?
   - Growth potential: For junior roles, does the candidate show learning ability?

3. **Technical Recruiter Score** — First-pass technical screening:
   - Required skills coverage: What % of "must have" skills are demonstrated?
   - Preferred skills coverage: What % of "nice to have" skills are present?
   - Experience level match: Does YoE and project complexity match the listing?
   - Red flags: Gaps, mismatches, overqualified/underqualified signals?

CRITICAL RULES:
- Be honest and strict. Don't inflate scores.
- A score of 85+ means "this resume would make it past this evaluator"
- If a score is below 85, provide SPECIFIC, ACTIONABLE improvement suggestions
- Improvements must NEVER fabricate experience — only reword, reorder, emphasize existing content
- The candidate is a real person. Only suggest changes based on what's already in the resume.

Return ONLY valid JSON (no markdown, no code fences):
{
    "ats_score": <0-100>,
    "ats_feedback": "<specific issues or 'Pass'>",
    "hiring_manager_score": <0-100>,
    "hm_feedback": "<specific issues or 'Pass'>",
    "tech_recruiter_score": <0-100>,
    "tr_feedback": "<specific issues or 'Pass'>",
    "improvements": ["<specific edit suggestion 1>", "<specific edit 2>", ...]
}"""


IMPROVE_SYSTEM_PROMPT = r"""You are an expert resume writer. You've been given a resume (either LaTeX or plain text sections) that scored below 85 on one or more evaluations (ATS, Hiring Manager, Technical Recruiter).

Apply the specific improvements listed below to raise ALL scores to 85+.

RULES:
1. NEVER fabricate experience, skills, projects, or metrics. Only reword, reorder, emphasize.
2. If the input is LaTeX: keep the exact same LaTeX structure and commands. Return the complete modified LaTeX source starting with \documentclass and ending with \end{document}.
3. If the input is a JSON dict of plain text sections: return an improved JSON dict with the same keys and plain text values. Return ONLY valid JSON, no markdown fences.
4. Make surgical, targeted edits — don't rewrite everything.
5. Focus on the specific feedback provided.
6. Keep the resume to ONE PAGE worth of content.
7. Use the job listing's terminology where the candidate genuinely has the skill.

WRITING STYLE RULES (enforce strictly):
- No em-dashes (—) used as connectors between clauses
- No AI filler phrases (e.g. "passionate about", "leveraged", "spearheaded", "results-driven", "dynamic", "synergy")
- Active voice, short punchy sentences
- Never fabricate experience, metrics, or skills not present in the original"""


IMPROVE_LATEX_SUFFIX = r"""
For LaTeX input: Return ONLY the complete, modified LaTeX source code. No explanations, no markdown fences.
Start with \documentclass and end with \end{document}."""


IMPROVE_TEXT_SUFFIX = """
For plain text JSON input: Return ONLY valid JSON with the same keys as the input dict. No explanations, no markdown fences. Example format:
{"SUMMARY": "improved text...", "SKILLS": "improved text...", "CLOVER_BULLETS": "improved text..."}"""


def score_resume(
    tailored_tex: str,
    job: Job,
    ai_client: AIClient,
) -> dict:
    """Score a tailored resume from 3 perspectives.

    Returns dict with ats_score, hiring_manager_score, tech_recruiter_score,
    and feedback for each.
    """
    prompt = f"""Score this tailored resume against the job listing:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description:
{job.description[:4000]}

TAILORED RESUME (LaTeX):
{tailored_tex}

Evaluate from all 3 perspectives (ATS, Hiring Manager, Technical Recruiter)."""

    try:
        result_text = ai_client.complete(
            prompt=prompt,
            system=SCORER_SYSTEM_PROMPT,
            temperature=0.2,
        )

        return extract_json(result_text)

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[SCORER] Error scoring resume for {job.company}: {e}")
        return {
            "ats_score": 0, "ats_feedback": "Error",
            "hiring_manager_score": 0, "hm_feedback": "Error",
            "tech_recruiter_score": 0, "tr_feedback": "Error",
            "improvements": [],
        }


def score_resume_text(
    sections: Dict[str, str],
    job: Job,
    ai_client: AIClient,
) -> dict:
    """Score a resume from plain text sections dict.

    Takes a dict like {"SUMMARY": "...", "SKILLS": "...", "CLOVER_BULLETS": "..."}
    and formats it as readable text for the scorer.

    Returns dict with ats_score, hiring_manager_score, tech_recruiter_score,
    and feedback for each.
    """
    # Format sections as readable text
    formatted_resume = "\n\n".join(
        f"=== {section_name} ===\n{content}"
        for section_name, content in sections.items()
        if content and content.strip()
    )

    prompt = f"""Score this tailored resume against the job listing:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Description:
{job.description[:4000]}

TAILORED RESUME (plain text):
{formatted_resume}

Evaluate from all 3 perspectives (ATS, Hiring Manager, Technical Recruiter)."""

    try:
        result_text = ai_client.complete(
            prompt=prompt,
            system=SCORER_SYSTEM_PROMPT,
            temperature=0.2,
        )

        return extract_json(result_text)

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[SCORER] Error scoring resume text for {job.company}: {e}")
        return {
            "ats_score": 0, "ats_feedback": "Error",
            "hiring_manager_score": 0, "hm_feedback": "Error",
            "tech_recruiter_score": 0, "tr_feedback": "Error",
            "improvements": [],
        }


def improve_resume(
    tailored_tex: str,
    job: Job,
    scores: dict,
    ai_client: AIClient,
) -> str:
    """Apply improvement suggestions to raise scores above 85."""
    improvements = scores.get("improvements", [])
    feedback_parts = []
    if scores.get("ats_score", 0) < 85:
        feedback_parts.append(f"ATS ({scores['ats_score']}): {scores.get('ats_feedback', '')}")
    if scores.get("hiring_manager_score", 0) < 85:
        feedback_parts.append(f"Hiring Manager ({scores['hiring_manager_score']}): {scores.get('hm_feedback', '')}")
    if scores.get("tech_recruiter_score", 0) < 85:
        feedback_parts.append(f"Tech Recruiter ({scores['tech_recruiter_score']}): {scores.get('tr_feedback', '')}")

    prompt = f"""Improve this resume based on the scoring feedback:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Description:
{job.description[:3000]}

SCORES & FEEDBACK (need all 85+):
{chr(10).join(feedback_parts)}

SPECIFIC IMPROVEMENTS TO MAKE:
{chr(10).join(f'- {imp}' for imp in improvements)}

CURRENT RESUME (LaTeX):
{tailored_tex}

Return the COMPLETE improved LaTeX source. Start with \\documentclass and end with \\end{{document}}."""

    system = IMPROVE_SYSTEM_PROMPT + IMPROVE_LATEX_SUFFIX

    try:
        improved = ai_client.complete(
            prompt=prompt,
            system=system,
            temperature=0.3,
            skip_cache=True,
        )
        improved = improved.strip()

        if improved.startswith("```"):
            lines = improved.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            improved = "\n".join(lines)

        if not improved.startswith("\\documentclass"):
            start = improved.find("\\documentclass")
            if start >= 0:
                improved = improved[start:]
            else:
                return tailored_tex  # fallback to original

        if "\\end{document}" not in improved:
            improved += "\n\\end{document}"

        return improved

    except Exception as e:
        logger.error(f"[SCORER] Error improving resume for {job.company}: {e}")
        return tailored_tex


def improve_resume_text(
    sections: Dict[str, str],
    job: Job,
    scores: dict,
    ai_client: AIClient,
) -> Dict[str, str]:
    """Apply improvements to plain text sections. Returns updated sections dict."""
    improvements = scores.get("improvements", [])
    feedback_parts = []
    if scores.get("ats_score", 0) < 85:
        feedback_parts.append(f"ATS ({scores['ats_score']}): {scores.get('ats_feedback', '')}")
    if scores.get("hiring_manager_score", 0) < 85:
        feedback_parts.append(f"Hiring Manager ({scores['hiring_manager_score']}): {scores.get('hm_feedback', '')}")
    if scores.get("tech_recruiter_score", 0) < 85:
        feedback_parts.append(f"Tech Recruiter ({scores['tech_recruiter_score']}): {scores.get('tr_feedback', '')}")

    sections_json = json.dumps(sections, indent=2)

    prompt = f"""Improve this resume based on the scoring feedback:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Description:
{job.description[:3000]}

SCORES & FEEDBACK (need all 85+):
{chr(10).join(feedback_parts)}

SPECIFIC IMPROVEMENTS TO MAKE:
{chr(10).join(f'- {imp}' for imp in improvements)}

CURRENT RESUME (plain text sections as JSON):
{sections_json}

Return ONLY a valid JSON object with the same keys and improved plain text values. No markdown fences."""

    system = IMPROVE_SYSTEM_PROMPT + IMPROVE_TEXT_SUFFIX

    try:
        improved_text = ai_client.complete(
            prompt=prompt,
            system=system,
            temperature=0.3,
            skip_cache=True,
        )
        improved_text = improved_text.strip()

        # Strip markdown fences if present
        if improved_text.startswith("```"):
            lines = improved_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            improved_text = "\n".join(lines).strip()

        improved_sections = extract_json(improved_text)

        # Validate: ensure all original keys are present; fall back per-key if missing
        result = dict(sections)  # start with originals
        for key, value in improved_sections.items():
            if key in result and isinstance(value, str):
                result[key] = value

        return result

    except Exception as e:
        logger.error(f"[SCORER] Error improving resume text for {job.company}: {e}")
        return sections


def score_and_improve(
    tailored_tex: str,
    job: Job,
    ai_client: AIClient,
    min_score: int = 85,
    max_rounds: int = 3,
    text_mode: bool = False,
    sections: Dict[str, str] | None = None,
) -> tuple[str, dict] | tuple[Dict[str, str], dict]:
    """Score a resume and iteratively improve it until all 3 scores are 85+.

    In LaTeX mode (default): returns (final_tex, final_scores).
    In text mode: pass text_mode=True and sections=<dict>; returns (final_sections, final_scores).
    """
    if text_mode:
        if sections is None:
            raise ValueError("sections dict must be provided when text_mode=True")
        return _score_and_improve_text(sections, job, ai_client, min_score, max_rounds)

    return _score_and_improve_latex(tailored_tex, job, ai_client, min_score, max_rounds)


def _score_and_improve_latex(
    tailored_tex: str,
    job: Job,
    ai_client: AIClient,
    min_score: int,
    max_rounds: int,
) -> tuple[str, dict]:
    """Internal: LaTeX score-and-improve loop."""
    current_tex = tailored_tex

    for round_num in range(1, max_rounds + 1):
        scores = score_resume(current_tex, job, ai_client)

        ats = scores.get("ats_score", 0)
        hm = scores.get("hiring_manager_score", 0)
        tr = scores.get("tech_recruiter_score", 0)

        if ats >= min_score and hm >= min_score and tr >= min_score:
            logger.info(f"Round {round_num}: ATS={ats}, HM={hm}, TR={tr} — all pass")
            return current_tex, scores

        logger.info(f"Round {round_num}: ATS={ats}, HM={hm}, TR={tr} — improving...")
        current_tex = improve_resume(current_tex, job, scores, ai_client)

    # Final score after last improvement
    final_scores = score_resume(current_tex, job, ai_client)
    ats = final_scores.get("ats_score", 0)
    hm = final_scores.get("hiring_manager_score", 0)
    tr = final_scores.get("tech_recruiter_score", 0)
    logger.info(f"Final: ATS={ats}, HM={hm}, TR={tr}")

    return current_tex, final_scores


def _score_and_improve_text(
    sections: Dict[str, str],
    job: Job,
    ai_client: AIClient,
    min_score: int,
    max_rounds: int,
) -> tuple[Dict[str, str], dict]:
    """Internal: plain text score-and-improve loop."""
    current_sections = dict(sections)

    for round_num in range(1, max_rounds + 1):
        scores = score_resume_text(current_sections, job, ai_client)

        ats = scores.get("ats_score", 0)
        hm = scores.get("hiring_manager_score", 0)
        tr = scores.get("tech_recruiter_score", 0)

        if ats >= min_score and hm >= min_score and tr >= min_score:
            logger.info(f"[TEXT] Round {round_num}: ATS={ats}, HM={hm}, TR={tr} — all pass")
            return current_sections, scores

        logger.info(f"[TEXT] Round {round_num}: ATS={ats}, HM={hm}, TR={tr} — improving...")
        current_sections = improve_resume_text(current_sections, job, scores, ai_client)

    # Final score after last improvement
    final_scores = score_resume_text(current_sections, job, ai_client)
    ats = final_scores.get("ats_score", 0)
    hm = final_scores.get("hiring_manager_score", 0)
    tr = final_scores.get("tech_recruiter_score", 0)
    logger.info(f"[TEXT] Final: ATS={ats}, HM={hm}, TR={tr}")

    return current_sections, final_scores
