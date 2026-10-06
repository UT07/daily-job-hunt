"""The anti-inflation rules were prompt text that nothing applied.

score_batch's system prompt has carried three of them since it was written:

    - If the resume lacks a REQUIRED skill, ATS score cannot exceed 75.
    - If the resume has no metrics, HM score cannot exceed 70.
    - If fewer than 3 of the top 5 required technologies are present, TR <= 75.

The only cap applied in code was apply_geo_score_cap (work authorisation), so
all three were requests — CLAUDE.md rule 4 — and all three are decidable, which
is what makes it a defect rather than a limitation.

Two are enforced from data the model ALREADY returns. The third is deliberately
NOT: identifying "the top 5 required technologies" needs a skills vocabulary,
which is the ESCO work in the persona-scoring design. A hand-rolled keyword
list would be a third unvalidated heuristic dressed as a check.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.score_caps import (  # noqa: E402
    BLOCKER_GAP_ATS_CAP,
    BLOCKER_GAP_ATS_CAP_JUNIOR,
    NO_METRICS_HM_CAP,
    apply_anti_inflation_caps,
    has_blocker_gap,
    quantified_bullet_count,
)

QUANTIFIED = (r"\begin{itemize}"
              r"\item Cut p99 checkout latency 40% by sharding the session store."
              r"\end{itemize}")
UNQUANTIFIED = (r"\begin{itemize}"
                r"\item Worked across the platform team on reliability."
                r"\end{itemize}")


def score(**kw):
    base = {"ats_score": 92, "hiring_manager_score": 88, "tech_recruiter_score": 85,
            "match_score": 88, "requirement_map": [], "gaps": []}
    base.update(kw)
    return base


class TestBlockerGap:
    def test_a_declared_blocker_caps_ats(self):
        s = apply_anti_inflation_caps(score(requirement_map=[
            {"requirement": "Kubernetes", "evidence": None, "severity": "blocker_gap"}]),
            QUANTIFIED)
        assert s["ats_score"] == BLOCKER_GAP_ATS_CAP

    def test_a_nice_to_have_gap_does_not_cap(self):
        """The rule names a REQUIRED skill. A nice-to-have is not one."""
        s = apply_anti_inflation_caps(score(requirement_map=[
            {"requirement": "Rust", "evidence": None, "severity": "nice_to_have_gap"},
            {"requirement": "AWS", "evidence": "5 years", "severity": "met"}]),
            QUANTIFIED)
        assert s["ats_score"] == 92

    def test_junior_roles_get_the_relaxed_cap_the_prompt_promises(self):
        s = apply_anti_inflation_caps(score(
            seniority="Junior/Graduate",
            requirement_map=[{"requirement": "k8s", "severity": "blocker_gap"}]),
            QUANTIFIED)
        assert s["ats_score"] == BLOCKER_GAP_ATS_CAP_JUNIOR

    def test_the_cap_is_a_ceiling_not_an_assignment(self):
        """A score already below the cap is left alone — capping must never
        RAISE a score, which an assignment would."""
        s = apply_anti_inflation_caps(score(
            ats_score=40,
            requirement_map=[{"requirement": "k8s", "severity": "blocker_gap"}]),
            QUANTIFIED)
        assert s["ats_score"] == 40

    def test_the_cap_is_recorded_in_gaps(self):
        s = apply_anti_inflation_caps(score(requirement_map=[
            {"requirement": "k8s", "severity": "blocker_gap"}]), QUANTIFIED)
        assert any("blocker_gap" in g for g in s["gaps"])

    def test_has_blocker_gap_tolerates_a_malformed_map(self):
        assert has_blocker_gap({"requirement_map": ["not a dict", None]}) is False
        assert has_blocker_gap({}) is False


class TestQuantification:
    def test_a_bullet_with_a_real_number_counts(self):
        assert quantified_bullet_count(QUANTIFIED) == 1

    def test_a_year_is_not_an_achievement(self):
        """"Jun 2022 - Jul 2024" is four digits twice and says nothing about
        impact. Counting it would make every resume quantified."""
        tex = r"\begin{itemize}\item Maintained the pipeline from 2022 to 2024.\end{itemize}"
        assert quantified_bullet_count(tex) == 0

    def test_an_unquantified_resume_caps_hm(self):
        s = apply_anti_inflation_caps(score(), UNQUANTIFIED)
        assert s["hiring_manager_score"] == NO_METRICS_HM_CAP

    def test_a_quantified_resume_is_not_capped(self):
        s = apply_anti_inflation_caps(score(), QUANTIFIED)
        assert s["hiring_manager_score"] == 88

    def test_no_resume_text_is_not_the_same_as_no_metrics(self):
        """An absent input must not be scored as a weak resume."""
        s = apply_anti_inflation_caps(score(), "")
        assert s["hiring_manager_score"] == 88


class TestMatchScoreInvariant:
    def test_match_score_never_exceeds_its_largest_component(self):
        """An average cannot exceed its largest element. Capping a perspective
        can break that, leaving an overall score above every score it averages
        — which reads as a bug to anyone who checks the arithmetic, and is."""
        s = apply_anti_inflation_caps(score(
            ats_score=92, hiring_manager_score=88, tech_recruiter_score=85,
            match_score=90,
            requirement_map=[{"requirement": "k8s", "severity": "blocker_gap"}]),
            UNQUANTIFIED)
        assert s["ats_score"] == 75 and s["hiring_manager_score"] == 70
        assert s["match_score"] <= max(s["ats_score"], s["hiring_manager_score"],
                                       s["tech_recruiter_score"])

    def test_a_compliant_result_is_untouched(self):
        """A cap that fires is evidence the model ignored its instruction. When
        it complied, every function here must be a no-op."""
        before = score(ats_score=70, hiring_manager_score=65,
                       tech_recruiter_score=68, match_score=68)
        after = apply_anti_inflation_caps(dict(before), QUANTIFIED)
        for k in ("ats_score", "hiring_manager_score", "tech_recruiter_score",
                  "match_score"):
            assert after[k] == before[k]

    def test_a_non_dict_is_returned_unchanged(self):
        assert apply_anti_inflation_caps(None) is None
        assert apply_anti_inflation_caps({}) == {}
