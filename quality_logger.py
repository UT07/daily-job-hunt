"""Quality logger — tracks AI model performance for each artifact generated.

Appends entries to output/ai_quality_log.jsonl for analysis by self_improver.py.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_PATH = Path("output/ai_quality_log.jsonl")


def log_quality(
    task: str,                      # "match", "tailor_resume", "tailor_text", "cover_letter"
    provider: str,
    model: str,
    job_id: str = "",
    company: str = "",
    job_title: str = "",
    scores: Optional[dict] = None,  # {ats_score, hiring_manager_score, tech_recruiter_score}
    quality_metrics: Optional[dict] = None,  # additional metrics
    success: bool = True,
    error: str = "",
):
    """Append a quality log entry."""
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "task": task,
        "provider": provider,
        "model": model,
        "job_id": job_id,
        "company": company,
        "job_title": job_title,
        "scores": scores or {},
        "quality_metrics": quality_metrics or {},
        "success": success,
        "error": error,
    }

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning(f"[QUALITY] Failed to write log: {e}")


def read_quality_log(limit: int = 1000) -> list[dict]:
    """Read recent quality log entries."""
    if not LOG_PATH.exists():
        return []
    entries = []
    with open(LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries[-limit:]


def get_model_stats() -> dict:
    """Compute per-model quality stats from the log.

    Returns: {
        "groq:llama-3.3-70b": {"count": 45, "avg_score": 82.3, "tasks": {"match": 20, "tailor": 25}},
        ...
    }
    """
    entries = read_quality_log()
    stats = {}
    for e in entries:
        key = f"{e['provider']}:{e['model']}"
        if key not in stats:
            stats[key] = {"count": 0, "total_score": 0, "scored_count": 0, "tasks": {}, "errors": 0}
        s = stats[key]
        s["count"] += 1
        s["tasks"][e["task"]] = s["tasks"].get(e["task"], 0) + 1
        if not e.get("success"):
            s["errors"] += 1
        scores = e.get("scores", {})
        if scores:
            avg = sum(scores.values()) / len(scores) if scores else 0
            s["total_score"] += avg
            s["scored_count"] += 1

    # Compute averages
    for key, s in stats.items():
        s["avg_score"] = round(s["total_score"] / s["scored_count"], 1) if s["scored_count"] > 0 else 0
        del s["total_score"]
        del s["scored_count"]

    return stats
