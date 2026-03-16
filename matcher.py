"""Job-resume matching engine using multi-provider AI client.

Scores each job against your resume profiles and determines
which resume variant is the best fit.
"""

from __future__ import annotations
import json
from typing import List, Dict
from scrapers.base import Job
from ai_client import AIClient


MATCH_SYSTEM_PROMPT = """You are an expert technical recruiter and career advisor. Your job is to score how well a candidate matches a job listing.

You will receive:
1. A candidate's resume content (LaTeX source)
2. A job listing (title, company, description)
3. The candidate's visa status and location preferences

Score the match from 0-100 based on:
- **Skills overlap** (40%): How many required/preferred skills does the candidate have?
- **Experience level fit** (25%): Does the candidate's experience match what's expected?
- **Role alignment** (20%): How closely does the job title/responsibilities match the candidate's background?
- **Location/visa fit** (15%): Can the candidate actually work at this location? Are they eligible?

IMPORTANT context about this candidate:
- They are a fresh MSc graduate with 2+ years of industry experience, so they're stronger than typical new grads
- They are an Indian citizen with Stamp 1G work authorization in Ireland
- They are targeting both fresh-grad/entry-level AND mid-level roles
- For remote roles outside Ireland, note they would need visa sponsorship

Return ONLY valid JSON (no markdown, no code fences):
{
    "score": <0-100>,
    "best_resume": "<sre_devops or fullstack>",
    "reasoning": "<2-3 sentences explaining the match>",
    "key_matches": ["<skill1>", "<skill2>", ...],
    "gaps": ["<missing skill/requirement>", ...],
    "tailoring_suggestions": ["<specific tweak to make resume stronger for this role>", ...]
}"""


def match_jobs(
    jobs: List[Job],
    resumes: Dict[str, str],
    ai_client: AIClient,
    min_score: int = 60,
) -> List[Job]:
    """Score and filter jobs against resume profiles.

    Returns only jobs that meet the minimum match score, sorted by score descending.
    """
    matched_jobs = []

    # Combine resumes into context
    resume_context = ""
    for key, tex in resumes.items():
        resume_context += f"\n\n=== RESUME VARIANT: {key} ===\n{tex}\n"

    for job in jobs:
        user_prompt = f"""Evaluate this job match:

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

            job.match_score = result.get("score", 0)
            job.match_reasoning = result.get("reasoning", "")
            job.matched_resume = result.get("best_resume", "fullstack")
            job._match_data = result

            if job.match_score >= min_score:
                matched_jobs.append(job)
                print(f"  [MATCH {job.match_score}%] {job.title} @ {job.company} -> {job.matched_resume}")
            else:
                print(f"  [SKIP  {job.match_score}%] {job.title} @ {job.company}")

        except json.JSONDecodeError as e:
            print(f"  [ERROR] Failed to parse match result for {job.title}: {e}")
            print(f"          Response was: {result_text[:200]}")
        except Exception as e:
            print(f"  [ERROR] Error matching {job.title}: {e}")

    matched_jobs.sort(key=lambda j: j.match_score, reverse=True)
    return matched_jobs
