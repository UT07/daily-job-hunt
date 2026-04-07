"""Self-improver helpers for Lambda — pure analysis functions with no disk I/O.

This module contains only the functions needed by the self_improve Lambda handler.
The full self_improver (local-run analysis, file I/O, YAML config updates) lives
at the repository root and is not included here to keep the Lambda bundle lean.
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def generate_adjustments(
    scraper_stats: dict = None,
    score_stats: dict = None,
    quality_stats: dict = None,
    model_stats: dict = None,
    keyword_stats: dict = None,
) -> list[dict]:
    """Analyze pipeline metrics and generate tiered adjustments.

    Returns a list of adjustment dicts, each with:
    - adjustment_type: what kind of change (scraper_config, score_threshold, ...)
    - risk_level: "low" | "medium" | "high"
    - status: "auto_applied" (low/medium) or "pending" (high, needs human review)
    - payload: the concrete change to apply
    - reason: human-readable explanation
    - evidence: raw data that triggered this adjustment
    """
    adjustments = []

    # Low-risk: disable broken scrapers (3-day zero yield)
    if scraper_stats:
        for name, stats in scraper_stats.items():
            yields = stats.get("yields", [])
            if len(yields) >= 3 and all(y == 0 for y in yields[-3:]):
                adjustments.append({
                    "adjustment_type": "scraper_config",
                    "risk_level": "low",
                    "status": "auto_applied",
                    "payload": {"scraper": name, "action": "disable"},
                    "reason": f"Scraper '{name}' returned 0 jobs for 3 consecutive runs",
                    "evidence": {"yields": yields},
                })

    # Low-risk: reorder sources by match rate if we have matched counts
    if scraper_stats:
        source_rates = {}
        for name, stats in scraper_stats.items():
            yields = stats.get("yields", [])
            matched = stats.get("matched", [])
            if yields and matched and len(yields) == len(matched):
                total_yielded = sum(yields)
                total_matched = sum(matched)
                if total_yielded > 0:
                    source_rates[name] = round(total_matched / total_yielded, 3)
        if source_rates:
            # Order from highest to lowest match rate
            ordered = sorted(source_rates, key=lambda k: source_rates[k], reverse=True)
            adjustments.append({
                "adjustment_type": "source_order",
                "risk_level": "low",
                "status": "auto_applied",
                "payload": {"sources_ranked": ordered, "match_rates": source_rates},
                "reason": "Reorder sources by historical match rate to prioritise high-signal scrapers",
                "evidence": {"source_rates": source_rates},
            })

    # Medium-risk: score threshold adjustment (too many low-scoring jobs)
    if score_stats:
        if score_stats.get("pct_below_50", 0) > 0.8:
            adjustments.append({
                "adjustment_type": "score_threshold",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"min_match_score": max(30, score_stats.get("avg_score", 50) - 10)},
                "previous_state": {"min_match_score": 50},
                "reason": (
                    f"{score_stats['pct_below_50']*100:.0f}% of jobs scored below 50 "
                    "— possible query-resume misalignment"
                ),
                "evidence": score_stats,
            })

        # Medium-risk: flag when all jobs cluster in C/D tiers (no S/A)
        tier_dist = score_stats.get("tier_distribution", {})
        high_tier_count = tier_dist.get("S", 0) + tier_dist.get("A", 0)
        total = score_stats.get("total", 0)
        if total >= 10 and high_tier_count == 0:
            adjustments.append({
                "adjustment_type": "score_threshold",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"min_match_score": max(40, score_stats.get("avg_score", 60) - 15)},
                "previous_state": {"min_match_score": 60},
                "reason": (
                    f"Zero S/A tier jobs in last 7 days ({total} scored). "
                    "Threshold may be too strict or queries need refinement."
                ),
                "evidence": tier_dist,
            })

    # High-risk: prompt change suggestion (compilation failure rate too high)
    if quality_stats:
        if quality_stats.get("trend") == "declining":
            fail_rate = quality_stats.get("compile_fail_rate", 0)
            if fail_rate > 0.2:
                adjustments.append({
                    "adjustment_type": "prompt_change",
                    "risk_level": "high",
                    "status": "pending",
                    "payload": {"target": "tailoring_prompt", "action": "review_and_update"},
                    "reason": (
                        f"LaTeX compilation failure rate is {fail_rate:.0%} — "
                        "AI tailoring prompt may be generating invalid LaTeX"
                    ),
                    "evidence": quality_stats,
                })

    return adjustments


def analyze_query_effectiveness(
    query_stats: dict, threshold: float = 0.05, min_runs: int = 3
) -> list[dict]:
    """Flag search queries (or sources) with consistently low match rates."""
    suggestions = []
    for query, stats in query_stats.items():
        rates = stats.get("match_rates", [])
        if len(rates) >= min_runs and all(r < threshold for r in rates[-min_runs:]):
            suggestions.append({
                "adjustment_type": "keyword_weight",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"query": query, "action": "suggest_modification"},
                "reason": (
                    f"Query/source '{query}' has <{threshold*100:.0f}% match rate "
                    f"for {min_runs}+ consecutive runs"
                ),
                "evidence": {"match_rates": rates},
            })
    return sorted(suggestions, key=lambda s: min(s["evidence"]["match_rates"]))


def analyze_keyword_gaps_for_resume(
    keyword_stats: dict, min_jobs: int = 25
) -> list[dict]:
    """Suggest base resume updates for consistently missing keywords.

    When keyword gap analysis across many matched JDs shows a skill appearing
    in ``min_jobs`` or more descriptions, it likely belongs on the base resume.
    Each suggestion is a medium-risk quality flag that the user reviews on the
    dashboard before approving changes to their base resume.
    """
    suggestions = []
    for keyword, stats in keyword_stats.items():
        if stats["count"] >= min_jobs:
            suggestions.append({
                "adjustment_type": "quality_flag",
                "risk_level": "medium",
                "status": "auto_applied",
                "notify": True,
                "payload": {"keyword": keyword, "action": "add_to_base_resume"},
                "reason": (
                    f"Consider adding '{keyword}' to base resume — "
                    f"appeared in {stats['count']} of top matched JDs "
                    f"(avg score: {stats['avg_job_score']})"
                ),
                "evidence": stats,
            })
    return sorted(suggestions, key=lambda s: s["evidence"]["count"], reverse=True)


def should_revert_or_extend(
    adjustment: dict, run_metrics: list[dict], threshold: float = 0.05
) -> str:
    """Evaluate an auto-applied adjustment after 3-5 runs.

    Decision logic:
    - Fewer than 4 data points: "wait" (not enough data).
    - 3-run average dropped > threshold: "revert".
    - 3-run average improved > threshold: "confirm".
    - Inconclusive at 3 runs:
        - If 6+ data points available, evaluate 5-run average.
        - Otherwise: "extend" (need more runs).

    Returns one of "wait", "revert", "confirm", or "extend".
    """
    if len(run_metrics) < 4:
        return "wait"
    before = run_metrics[0].get("avg_base_score", 0)
    if before == 0:
        return "wait"

    after_3 = sum(r.get("avg_base_score", 0) for r in run_metrics[1:4]) / 3
    change_3 = (after_3 - before) / before

    if change_3 < -threshold:
        return "revert"
    if change_3 > threshold:
        return "confirm"

    # Inconclusive at 3 runs — try 5 if available
    if len(run_metrics) >= 6:
        after_5 = sum(r.get("avg_base_score", 0) for r in run_metrics[1:6]) / 5
        change_5 = (after_5 - before) / before
        if change_5 < -threshold:
            return "revert"
        return "confirm"

    return "extend"


def execute_revert(db, adjustment: dict, cooldown_runs: int = 5):
    """Revert an adjustment and set a cooldown period.

    Marks the adjustment as "reverted" in the database and, if the adjustment
    stored its ``previous_state``, inserts a new auto-applied adjustment that
    restores the prior configuration.
    """
    cooldown_until = (datetime.now() + timedelta(days=cooldown_runs)).isoformat()
    db.table("pipeline_adjustments").update({
        "status": "reverted",
        "reverted_at": datetime.now().isoformat(),
        "cooldown_until": cooldown_until,
    }).eq("id", adjustment["id"]).execute()

    if adjustment.get("previous_state"):
        db.table("pipeline_adjustments").insert({
            "user_id": adjustment["user_id"],
            "adjustment_type": adjustment["adjustment_type"],
            "risk_level": "low",
            "status": "auto_applied",
            "payload": adjustment["previous_state"],
            "reason": f"Auto-revert of adjustment {adjustment['id']}",
            "evidence": {"reverted_from": adjustment["id"]},
        }).execute()


def save_pipeline_run(db, user_id: str, run_data: dict):
    """Save pipeline run metrics to Supabase pipeline_runs table."""
    db.table("pipeline_runs").insert({
        "user_id": user_id,
        "started_at": run_data.get("started_at"),
        "completed_at": datetime.now().isoformat(),
        "jobs_scraped": run_data.get("jobs_scraped", 0),
        "jobs_new": run_data.get("jobs_new", 0),
        "jobs_scored": run_data.get("jobs_scored", 0),
        "jobs_matched": run_data.get("jobs_matched", 0),
        "jobs_tailored": run_data.get("jobs_tailored", 0),
        "avg_base_score": run_data.get("avg_base_score"),
        "avg_final_score": run_data.get("avg_final_score"),
        "avg_writing_quality": run_data.get("avg_writing_quality"),
        "active_adjustments": run_data.get("active_adjustments"),
        "scraper_stats": run_data.get("scraper_stats"),
        "model_stats": run_data.get("model_stats"),
        "status": "completed",
    }).execute()
