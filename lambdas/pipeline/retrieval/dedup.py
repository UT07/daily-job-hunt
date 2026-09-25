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
#
# Started at 0.93 per the design doc. Task 15 swept {0.88, 0.90, 0.92, 0.93,
# 0.95, 0.97} against the real, freshly-backfilled embeddings for all 1,232
# jobs and hand-inspected the actual pairs at each level (not just counts).
# 0.93 gives 104 pairs, but includes confirmed false positives -- pulling
# the full descriptions, not just titles, showed:
#   - Google, 0.9627: "Senior Systems Engineer, SRE" vs "Senior Software
#     Engineer, SRE" are two genuinely distinct, officially separate Google
#     SRE hiring tracks (different minimum-qualifications text: systems/
#     Unix-internals vs software-engineering focus) -- not a duplicate.
#   - Datadog, 0.9609: "Senior Sales Engineer - Key Accounts Southcentral"
#     vs "...Key Accounts" target different account tiers (regional vs
#     Fortune-100 strategic) in the body text -- distinct reqs.
# Both survive even at 0.95 (0.9609 and 0.9627 are both >= 0.95), so 0.95
# is not safe either. Raised to 0.97 (56 -> 21 pairs): every pair sampled
# at 0.97, including the lowest-similarity member of that band (Twilio,
# 0.9715), checked out as a genuine same-posting duplicate on full-text
# inspection -- several with the SAME defect shape as the original TREQS
# report (Mastercard "Lead Site Reliability Engineer" vs "Lead BizOps
# Engineer" at 0.9743: the first row's title was mis-scraped, its own
# description body reads "seeking a Lead BizOps Engineer" throughout).
# A missed real duplicate just leaves both copies visible to the user;
# a false merge silently drops one of two genuinely different open roles.
# That asymmetry favors the higher, still-data-backed threshold.
SEMANTIC_THRESHOLD = 0.97

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
