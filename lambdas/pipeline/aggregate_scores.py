"""Aggregate scoring results from parallel Map state chunks.

Merges matched_items arrays from each ScoreBatch chunk invocation into
a single result with the same shape as the original ScoreBatch output,
so downstream states (ProcessMatchedJobs) work unchanged.
"""
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    chunks = event.get("score_chunks", [])

    all_matched = []
    total_matched = 0
    total_skipped = 0

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        all_matched.extend(chunk.get("matched_items", []))
        total_matched += chunk.get("matched_count", 0)
        total_skipped += chunk.get("skipped_count", 0)

    logger.info(
        f"[aggregate_scores] {len(chunks)} chunks → "
        f"{total_matched} matched, {total_skipped} skipped"
    )
    return {
        "matched_items": all_matched,
        "matched_count": total_matched,
        "skipped_count": total_skipped,
    }
