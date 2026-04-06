"""Split a list of job hashes into scoring chunks.

Each chunk is a self-contained object with hashes + context (user_id,
min_match_score) so the Map state iterator can pass it directly to
ScoreBatchFunction without needing parent-context injection.
"""
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    hashes = event.get("new_job_hashes", [])
    user_id = event.get("user_id", "")
    min_match_score = event.get("min_match_score", 60)
    chunk_size = event.get("chunk_size", 25)

    chunks = []
    for i in range(0, len(hashes), chunk_size):
        chunks.append({
            "new_job_hashes": hashes[i:i + chunk_size],
            "user_id": user_id,
            "min_match_score": min_match_score,
        })

    logger.info(f"[chunk_hashes] {len(hashes)} hashes → {len(chunks)} chunks of ≤{chunk_size}")
    return {"chunks": chunks, "total": len(hashes), "num_chunks": len(chunks)}
