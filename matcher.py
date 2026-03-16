"""Job-resume matching engine using 3-perspective scoring.

Scores each job against the candidate's BASE resume from three viewpoints:
1. ATS (Applicant Tracking System) — keyword match, formatting, structure
2. Hiring Manager — impact, relevance, culture fit, growth potential
3. Technical Recruiter — required/preferred skills coverage, experience level

A job passes matching if the average of all 3 scores meets the threshold.
After tailoring, the same 3 scores must each individually be 85+.
"""

from __future__ import annotations
import json
from typing import List, Dict
from scrapers.base import Job
from ai_client import AIClient


MATCH_SYSTEM_PROMPT = """You are an expert job-candidate evaluator. Score how well a candidate's resume matches a job listing from THREE distinct perspectives.

SCORING PERSPECTIVES (each 0-100):

1. **ATS Score** — Applicant Tracking System:
   - Keyword match: Does the resume contain the key terms from the job description?
   - Section structure: Standard sections present (Experience, Education, Skills)?
   - Job title alignment: Does the candidate's background map to this role?
   - Hard requirements: Years of experience, degree level, certifications

2. **Hiring Manager Score** — The person who manages this hire:
   - Relevant impact: Do accomplishments relate to what this team needs?
   - Experience narrative: Does the career story make sense for this position?
   - Culture/team fit signals: Would this person thrive in this environment?
   - Growth potential: For junior roles, does the candidate show strong learning ability?

3. **Technical Recruiter Score** — First-pass technical screening:
   - Required skills coverage: What % of "must have" skills are demonstrated?
   - Preferred skills coverage: What % of "nice to have" skills are present?
   - Experience level match: Does YoE and project complexity match?
   - Red flags: Gaps, mismatches, overqualified/underqualified signals?

IMPORTANT candidate context:
- Fresh MSc Cloud Computing graduate with 2+ years of industry experience
- Indian citizen with Stamp 1G work authorization in Ireland (full-time eligible)
- Targeting both fresh-grad/entry-level AND mid-level roles
- For remote roles outside Ireland, would need visa sponsorship
- Has two resume variants: sre_devops (SRE/DevOps/Platform) and fullstack (Full-Stack/Backend)

Be honest and strict. Don't inflate scores — this determines which jobs are worth applying to.

Return ONLY valid JSON (no markdown, no code fences):
{
    "ats_score": <0-100>,
    "hiring_manager_score": <0-100>,
    "tech_recruiter_score": <0-100>,
    "best_resume": "<sre_devops or fullstack>",
    "reasoning": "<2-3 sentences explaining the match and which perspective scored lowest and why>",
    "key_matches": ["<skill1>", "<skill2>", ...],
    "gaps": ["<missing skill/requirement>", ...],
    "tailoring_suggestions": ["<specific tweak to make resume stronger>", ...]
}"""


def match_jobs(
    jobs: List[Job],
    resumes: Dict[str, str],
    ai_client: AIClient,
    min_score: int = 60,
) -> List[Job]:
    """Score and filter jobs using 3-perspective evaluation.

    A job passes if the AVERAGE of (ATS, HM, TR) meets min_score.
    Returns matched jobs sorted by average score descending.
    """
    matched_jobs = []

    # Combine resumes into context
    resume_context = ""
    for key, tex in resumes.items():
        resume_context += f"\n\n=== RESUME VARIANT: {key} ===\n{tex}\n"

    for job in jobs:
        user_prompt = f"""Evaluate this job match from all 3 perspectives:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Remote: {job.remote}
- Salary: {job.salary or 'Not specified'}
- Type: {job.job_type or 'Not specified'}

JOB DESCRIPTION:
{job.description[:4000]}

CANDIDATE RESUMES:
{resume_context}

CANDIDATE INFO:
- Location: Dublin, Ireland
- Visa: Stamp 1G (eligible for full-time work in Ireland)
- Citizenship: Indian (would need sponsorship for roles outside Ireland)
- Target: Fresh grad roles (where experience gives an edge) and mid-level roles"""

        try:
            result_text = ai_client.complete(
                prompt=user_prompt,
                system=MATCH_SYSTEM_PROMPT,
                temperature=0.3,
            )

            # Clean up response — some models wrap in code fences
            result_text = result_text.strip()
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            result_text = result_text.strip()

            result = json.loads(result_text)

            ats = result.get("ats_score", 0)
            hm = result.get("hiring_manager_score", 0)
            tr = result.get("tech_recruiter_score", 0)
            avg_score = round((ats + hm + tr) / 3, 1)

            # Store all scores on the job object
            job.ats_score = ats
            job.hiring_manager_score = hm
            job.tech_recruiter_score = tr
            job.match_score = avg_score
            job.match_reasoning = result.get("reasoning", "")
            job.matched_resume = result.get("best_resume", "fullstack")
            job._match_data = result

            if avg_score >= min_score:
                matched_jobs.append(job)
                print(f"  [MATCH] {job.title} @ {job.company} — ATS={ats} HM={hm} TR={tr} (avg={avg_score}) -> {job.matched_resume}")
            else:
                print(f"  [SKIP]  {job.title} @ {job.company} — ATS={ats} HM={hm} TR={tr} (avg={avg_score})")

        except json.JSONDecodeError as e:
            print(f"  [ERROR] Failed to parse match result for {job.title}: {e}")
            print(f"          Response was: {result_text[:200]}")
        except Exception as e:
            print(f"  [ERROR] Error matching {job.title}: {e}")

    matched_jobs.sort(key=lambda j: j.match_score, reverse=True)
    return matched_jobs
