"""Evaluation metrics.

Tier accuracy allows one tier of tolerance because the scorer is a language
model, not a classifier with a ground truth; demanding exact agreement would
make the gate fire on noise. Two tiers off is a real regression.
"""
import statistics

TIER_ORDER = ["S", "A", "B", "C", "D"]


def _tier_distance(a: str, b: str) -> int:
    return abs(TIER_ORDER.index(a) - TIER_ORDER.index(b))


def tier_accuracy(results: list[dict]) -> float:
    scored = [r for r in results if r.get("expected_tier")]
    if not scored:
        return 1.0
    ok = sum(1 for r in scored if _tier_distance(r["expected_tier"], r["actual_tier"]) <= 1)
    return ok / len(scored)


def fabrication_rate(results: list[dict]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.get("fabricated")) / len(results)


def guard_pass_rate(results: list[dict]) -> float:
    if not results:
        return 1.0
    return sum(1 for r in results if r.get("guards_passed")) / len(results)


def score_variance(results: list[dict]) -> float:
    """Mean variance of repeated scores for the same input.

    Measures the non-determinism defect: the same job scoring differently
    across runs.
    """
    spreads = [statistics.pvariance(r["scores"]) for r in results if len(r.get("scores", [])) > 1]
    return statistics.mean(spreads) if spreads else 0.0


def latency_percentiles(results: list[dict]) -> dict:
    values = sorted(r["latency_s"] for r in results if "latency_s" in r)
    if not values:
        return {"p50": 0.0, "p95": 0.0}
    return {
        "p50": values[len(values) // 2],
        "p95": values[max(int(len(values) * 0.95) - 1, 0)],
    }


def summarise(results: list[dict]) -> dict:
    return {
        "n": len(results),
        "tier_accuracy": round(tier_accuracy(results), 4),
        "fabrication_rate": round(fabrication_rate(results), 4),
        "guard_pass_rate": round(guard_pass_rate(results), 4),
        "score_variance": round(score_variance(results), 4),
        **{k: round(v, 3) for k, v in latency_percentiles(results).items()},
    }
