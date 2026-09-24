"""Tier-4 semantic dedup.

merge_dedup already applies exact hash, exact company+title, and fuzzy title.
None of those catch the same role scraped under different queries with
different wording — the defect where "Backend Software Engineer @ TREQS"
was scored both 78 and 68.
"""
import logging

from retrieval.embeddings import embed
from retrieval.store import similar_jobs_in_company

logger = logging.getLogger()

# Tuned against labelled duplicate pairs from production data, not guessed.
SEMANTIC_THRESHOLD = 0.93

MIN_DESCRIPTION_CHARS = 200


def find_semantic_duplicate(job: dict) -> dict | None:
    """Return an already-seen job that is semantically the same posting."""
    description = (job.get("description") or "").strip()
    if len(description) < MIN_DESCRIPTION_CHARS:
        # Embedding a near-empty description produces a vector close to every
        # other near-empty description; that would merge unrelated roles.
        return None

    company = (job.get("company") or "").strip()
    if not company:
        return None

    vector = embed(f"{job.get('title', '')}\n\n{description}")
    for match in similar_jobs_in_company(company, vector, SEMANTIC_THRESHOLD):
        if match.get("job_hash") != job.get("job_hash"):
            logger.info(
                "[dedup] semantic match: %s ~ %s (%.3f)",
                job.get("job_hash"), match["job_hash"], match.get("similarity", 0),
            )
            return match
    return None
