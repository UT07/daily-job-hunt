"""The anti-inflation rules, applied instead of merely requested.

`score_batch`'s system prompt has carried these since it was written:

    - If the resume lacks a REQUIRED skill explicitly stated in the JD,
      ATS score cannot exceed 75.
    - If the resume has no metrics or quantified achievements relevant to the
      role, HM score cannot exceed 70.
    - If fewer than 3 of the top 5 required technologies listed in the JD are
      present in the resume, TR score cannot exceed 75.

Nothing enforced any of them. The only cap applied in code was
`apply_geo_score_cap` (work authorisation), so these three were prompt-level
requests — CLAUDE.md rule 4 — and all three are decidable, which is what makes
it a defect rather than a limitation.

Two are implemented here from data the model ALREADY returns. The third is not,
and saying so is the point: "the top 5 required technologies" needs a skills
vocabulary to identify technologies at all, which is the ESCO work in
docs/superpowers/specs/2026-10-06-persona-scoring-design.md §4. Implementing it
with a hand-rolled keyword list would be a third unvalidated heuristic wearing
the costume of a check.

A cap that fires is evidence the model ignored its own instruction. On a run
where it complied, every function here is a no-op — which is the correct
behaviour for a guard and the reason this is safe to apply unconditionally.
"""
from __future__ import annotations

import re
from typing import Any

# From the prompt, including its junior/graduate relaxation ("ATS cap becomes
# 80, TR cap becomes 80"). Kept as named constants so the prompt and the check
# can be diffed against each other rather than compared by eye.
BLOCKER_GAP_ATS_CAP = 75
BLOCKER_GAP_ATS_CAP_JUNIOR = 80
NO_METRICS_HM_CAP = 70

_JUNIOR_SENIORITY = ("junior", "graduate", "entry", "associate", "intern")

_PERSPECTIVES = ("ats_score", "hiring_manager_score", "tech_recruiter_score")

# A bullet's text, bounded so a runaway match cannot swallow the document.
_BULLET = re.compile(r"\\item\s+(.{0,400}?)(?=\\item|\\end\{itemize\}|$)", re.DOTALL)

# A year is not an achievement. "Jun 2022 - Jul 2024" is four digits twice and
# says nothing about impact, so a bare 4-digit year in 1900-2099 does not count
# as quantification. Everything else does: 40%, 150 endpoints, p99, $2.4M, 12x.
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_DIGIT = re.compile(r"\d")


def _is_junior(score_result: dict[str, Any]) -> bool:
    seniority = str(score_result.get("seniority") or "").lower()
    return any(word in seniority for word in _JUNIOR_SENIORITY)


def has_blocker_gap(score_result: dict[str, Any]) -> bool:
    """A JD requirement the resume does not satisfy, as the model itself said.

    Read from `requirement_map`, which the prompt already asks for with a
    `severity` of met | nice_to_have_gap | blocker_gap. The model is being held
    to its OWN finding rather than to a second opinion -- there is no new
    judgement here, only the refusal to let a blocker_gap sit beside a 90.
    """
    for entry in score_result.get("requirement_map") or []:
        if isinstance(entry, dict) and entry.get("severity") == "blocker_gap":
            return True
    return False


def quantified_bullet_count(resume_text: str) -> int:
    """Bullets carrying a number that is not merely a year."""
    count = 0
    for match in _BULLET.finditer(resume_text or ""):
        body = _YEAR.sub(" ", match.group(1))
        if _DIGIT.search(body):
            count += 1
    return count


def apply_anti_inflation_caps(score_result: dict[str, Any],
                              resume_text: str = "") -> dict[str, Any]:
    """Apply the decidable anti-inflation rules. Mutates and returns.

    Deliberately mirrors `apply_geo_score_cap`: pure data, no AI call, no DB
    query, deterministic, and it annotates `gaps[]` so the cap is visible to
    whoever reads the row rather than only to whoever reads the code.
    """
    if not score_result or not isinstance(score_result, dict):
        return score_result

    gaps = score_result.setdefault("gaps", [])
    if not isinstance(gaps, list):
        gaps = score_result["gaps"] = []

    if has_blocker_gap(score_result):
        cap = BLOCKER_GAP_ATS_CAP_JUNIOR if _is_junior(score_result) else BLOCKER_GAP_ATS_CAP
        if isinstance(score_result.get("ats_score"), (int, float)) \
                and score_result["ats_score"] > cap:
            score_result["ats_score"] = cap
            marker = f"ats_capped_{cap}_blocker_gap"
            if marker not in gaps:
                gaps.append(marker)

    # Only when a resume was supplied. An empty string means the caller had no
    # resume text to judge, which is NOT the same as a resume with no metrics,
    # and capping on it would punish a missing input rather than a weak resume.
    if resume_text and quantified_bullet_count(resume_text) == 0:
        if isinstance(score_result.get("hiring_manager_score"), (int, float)) \
                and score_result["hiring_manager_score"] > NO_METRICS_HM_CAP:
            score_result["hiring_manager_score"] = NO_METRICS_HM_CAP
            marker = f"hm_capped_{NO_METRICS_HM_CAP}_no_quantified_achievements"
            if marker not in gaps:
                gaps.append(marker)

    # An average cannot exceed its largest element. Capping a perspective can
    # break that, leaving an overall score above every score it averages --
    # which reads as a bug to anyone who checks the arithmetic, and is one.
    # The weights are not published in the prompt, so the invariant is enforced
    # rather than the average recomputed.
    components = [score_result[k] for k in _PERSPECTIVES
                  if isinstance(score_result.get(k), (int, float))]
    if components and isinstance(score_result.get("match_score"), (int, float)):
        ceiling = max(components)
        if score_result["match_score"] > ceiling:
            score_result["match_score"] = ceiling

    return score_result
