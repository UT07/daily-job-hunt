"""Resume tailoring engine using Claude API.

Takes the base LaTeX resume and tweaks it for each specific job,
emphasizing relevant skills, adjusting the summary, and reordering bullet points.
"""

from __future__ import annotations
import json
import anthropic
from pathlib import Path
from scrapers.base import Job


TAILOR_SYSTEM_PROMPT = r"""You are an expert resume writer who specializes in tailoring technical resumes for specific job listings. You work with LaTeX resumes.

RULES:
1. NEVER fabricate experience, skills, or accomplishments. Only reword, reorder, and emphasize what already exists.
2. Keep the exact same LaTeX structure, commands, and formatting.
3. Make targeted, surgical edits — don't rewrite the entire resume.
4. Focus changes on:
   - **Summary section**: Adjust emphasis to highlight the most relevant skills/experience for this role
   - **Skills section**: Reorder skills to put the most relevant ones first; adjust groupings if needed
   - **Experience bullets**: Reorder bullets within each job to put the most relevant first; tweak wording to use the job listing's terminology where truthful
   - **Projects**: If one project is particularly relevant, emphasize it
5. The resume must remain truthful and represent the candidate's actual experience.
6. Keep the resume to ONE PAGE — do not add content that would push it past one page.
7. If the job mentions specific technologies the candidate has used, make sure those are prominently placed.

Return ONLY the complete, modified LaTeX source code. No explanations, no markdown fences, just pure LaTeX starting with \documentclass."""


def tailor_resume(
    job: Job,
    base_tex: str,
    api_key: str,
    output_dir: Path,
    model: str = "claude-sonnet-4-20250514",
    temperature: float = 0.3,
) -> str:
    """Tailor a LaTeX resume for a specific job listing.

    Returns the path to the tailored .tex file.
    """
    client = anthropic.Anthropic(api_key=api_key)

    # Get tailoring suggestions from the matching step if available
    suggestions = ""
    if hasattr(job, "_match_data") and job._match_data:
        sugg_list = job._match_data.get("tailoring_suggestions", [])
        key_matches = job._match_data.get("key_matches", [])
        gaps = job._match_data.get("gaps", [])
        suggestions = f"""
TAILORING CONTEXT FROM MATCHING ANALYSIS:
- Key skill matches: {', '.join(key_matches)}
- Gaps to address (de-emphasize or contextualize): {', '.join(gaps)}
- Specific suggestions: {'; '.join(sugg_list)}"""

    user_prompt = f"""Tailor this resume for the following job:

JOB LISTING:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Type: {job.job_type or 'Full-time'}

JOB DESCRIPTION:
{job.description[:4000]}
{suggestions}

BASE RESUME (LaTeX):
{base_tex}

Return the COMPLETE tailored LaTeX source. Start with \\documentclass and end with \\end{{document}}."""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=temperature,
            system=TAILOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        tailored_tex = response.content[0].text.strip()

        # Sanity check: must start with \documentclass
        if not tailored_tex.startswith("\\documentclass"):
            # Try to extract LaTeX from response
            start = tailored_tex.find("\\documentclass")
            if start >= 0:
                tailored_tex = tailored_tex[start:]
            else:
                print(f"  [WARN] Tailored resume for {job.company} doesn't look like LaTeX, using base")
                tailored_tex = base_tex

        # Ensure it ends with \end{document}
        if "\\end{document}" not in tailored_tex:
            tailored_tex += "\n\\end{document}"

        # Save tailored .tex file
        safe_company = "".join(c for c in job.company if c.isalnum() or c in " _-")[:30].strip()
        safe_title = "".join(c for c in job.title if c.isalnum() or c in " _-")[:30].strip()
        filename = f"resume_{safe_company}_{safe_title}".replace(" ", "_")
        tex_path = output_dir / f"{filename}.tex"
        tex_path.write_text(tailored_tex, encoding="utf-8")

        job.tailored_tex_path = str(tex_path)
        print(f"  [TAILORED] {job.title} @ {job.company} -> {tex_path.name}")
        return str(tex_path)

    except anthropic.APIError as e:
        print(f"  [ERROR] API error tailoring for {job.company}: {e}")
        return ""
